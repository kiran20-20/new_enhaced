from flask import Flask, render_template, request, session, redirect, url_for, send_from_directory, make_response
import googlemaps
import polyline
import folium
from datetime import datetime
from flask_session import Session
from branca.element import Template, MacroElement
import os
import pandas as pd
import json
import glob
from uuid import uuid4
import numpy as np
from geopy.distance import geodesic
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

API_KEY = os.environ.get("API_KEY")
gmaps = googlemaps.Client(key=API_KEY)

# Enhanced coordinate interpolation for higher density
def interpolate_coordinates(coords, target_points_per_km=1000):
    """
    Interpolate coordinates to achieve target density per kilometer
    """
    if len(coords) < 2:
        return coords
    
    interpolated = [coords[0]]
    
    for i in range(len(coords) - 1):
        start = coords[i]
        end = coords[i + 1]
        
        # Calculate distance between points
        distance_km = geodesic(start, end).kilometers
        
        if distance_km > 0:
            # Calculate number of points needed
            points_needed = max(1, int(distance_km * target_points_per_km))
            
            # Interpolate points
            for j in range(1, points_needed + 1):
                ratio = j / points_needed
                lat = start[0] + (end[0] - start[0]) * ratio
                lng = start[1] + (end[1] - start[1]) * ratio
                interpolated.append((lat, lng))
    
    return interpolated

def calculate_turn_angle(p1, p2, p3):
    """
    Calculate turn angle between three consecutive points
    Returns angle in degrees (0-180)
    """
    # Convert to vectors
    v1 = (p2[0] - p1[0], p2[1] - p1[1])
    v2 = (p3[0] - p2[0], p3[1] - p2[1])
    
    # Calculate dot product and magnitudes
    dot_product = v1[0] * v2[0] + v1[1] * v2[1]
    mag1 = math.sqrt(v1[0]**2 + v1[1]**2)
    mag2 = math.sqrt(v2[0]**2 + v2[1]**2)
    
    if mag1 == 0 or mag2 == 0:
        return 0
    
    # Calculate angle
    cos_angle = dot_product / (mag1 * mag2)
    cos_angle = max(-1, min(1, cos_angle))  # Clamp to valid range
    angle = math.degrees(math.acos(cos_angle))
    
    return angle

def detect_hazardous_areas(coords, steps):
    """
    Detect various types of hazardous areas along the route
    """
    hazards = []
    
    # Analyze each segment for potential hazards
    for i in range(len(coords) - 2):
        p1, p2, p3 = coords[i], coords[i+1], coords[i+2]
        
        # Calculate turn angle
        turn_angle = calculate_turn_angle(p1, p2, p3)
        
        # Blind turn detection (sharp turns > 45 degrees)
        if turn_angle > 45:
            hazard_type = "blind_turn"
            if turn_angle > 90:
                severity = "high"
            elif turn_angle > 70:
                severity = "medium"
            else:
                severity = "low"
            
            hazards.append({
                'location': p2,
                'type': hazard_type,
                'severity': severity,
                'angle': turn_angle,
                'description': f"Sharp turn ({turn_angle:.1f}°)"
            })
    
    # Analyze steps for additional hazards
    for step in steps:
        instruction = step['html_instructions'].lower()
        location = (step['end_location']['lat'], step['end_location']['lng'])
        
        # Detect specific hazard keywords
        if any(keyword in instruction for keyword in ['merge', 'ramp', 'exit']):
            hazards.append({
                'location': location,
                'type': 'merge_point',
                'severity': 'medium',
                'description': 'Highway merge/exit point'
            })
        
        if any(keyword in instruction for keyword in ['roundabout', 'circle']):
            hazards.append({
                'location': location,
                'type': 'roundabout',
                'severity': 'medium',
                'description': 'Roundabout ahead'
            })
    
    return hazards

def fetch_accident_data(coords, gmaps_client):
    """
    Fetch accident-prone areas using Places API
    """
    accident_zones = []
    
    # Sample every 50th coordinate to avoid API limits
    sample_coords = coords[::50]
    
    def fetch_accidents_for_location(coord):
        try:
            # Search for accident-related keywords
            places = gmaps_client.places_nearby(
                location=coord,
                radius=500,
                keyword='accident prone traffic police'
            )
            
            local_accidents = []
            for place in places.get('results', []):
                if any(keyword in place['name'].lower() 
                      for keyword in ['accident', 'crash', 'collision', 'traffic', 'police']):
                    local_accidents.append({
                        'name': place['name'],
                        'location': (
                            place['geometry']['location']['lat'],
                            place['geometry']['location']['lng']
                        ),
                        'type': 'accident_prone'
                    })
            return local_accidents
        except Exception as e:
            print(f"Error fetching accident data: {e}")
            return []
    
    # Use threading for faster API calls
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_coord = {executor.submit(fetch_accidents_for_location, coord): coord 
                          for coord in sample_coords}
        
        for future in as_completed(future_to_coord):
            accident_zones.extend(future.result())
    
    return accident_zones

@app.route('/')
def home():
    df = pd.read_excel("IOCL_Landmark_Details.xlsx")
    landmarks = [
        {'name': row['Landmark Name'], 'lat': row['Latitude'], 'lng': row['Longitude']}
        for _, row in df.iterrows()
    ]
    return render_template("route_form.html", landmarks=landmarks)

@app.route('/fetch_routes', methods=['POST'])
def fetch_routes():
    # Clear session and old route files
    session.clear()
    for f in glob.glob("templates/route_preview_*.html"):
        os.remove(f)
    for f in glob.glob("templates/route_map_*.html"):
        os.remove(f)

    source = request.form['source']
    destination = request.form['destination']
    vehicle = request.form['vehicle']

    try:
        source_coords = tuple(map(float, source.split(',')))
        dest_coords = tuple(map(float, destination.split(',')))
    except ValueError:
        return "Invalid coordinates"

    directions = gmaps.directions(
        source_coords, dest_coords,
        mode=vehicle,
        alternatives=True,
        departure_time=datetime.now()
    )

    if not directions:
        return "No routes found."

    session['directions'] = directions
    session['source'] = source_coords
    session['destination'] = dest_coords
    session['vehicle'] = vehicle
    session.modified = True

    routes = []
    for i, route in enumerate(directions):
        coords = polyline.decode(route['overview_polyline']['points'])
        distance = route['legs'][0]['distance']['text']
        duration = route['legs'][0]['duration']['text']
        summary = route.get('summary', f"Route {i+1}")

        unique_id = uuid4().hex
        preview_file = f"route_preview_{i}_{unique_id}.html"
        m = folium.Map(location=coords[len(coords)//2], zoom_start=12)
        folium.PolyLine(coords, color='blue', weight=5).add_to(m)
        m.save(f"templates/{preview_file}")

        routes.append({
            'index': i,
            'distance': distance,
            'duration': duration,
            'summary': summary,
            'preview_file': preview_file
        })

    return render_template("route_select.html", routes=routes)

@app.route('/analyze_route', methods=['POST'])
def analyze_route():
    directions = session.get('directions')
    index = int(request.form['route_index'])

    if not directions or index >= len(directions):
        return "Invalid route selected or data expired."

    selected = directions[index]
    steps = selected['legs'][0]['steps']
    original_coords = polyline.decode(selected['overview_polyline']['points'])
    source = session['source']
    destination = session['destination']

    # Enhanced coordinate interpolation for higher density
    print(f"Original coordinates: {len(original_coords)}")
    enhanced_coords = interpolate_coordinates(original_coords, target_points_per_km=1000)
    print(f"Enhanced coordinates: {len(enhanced_coords)}")

    # Detect hazardous areas
    hazards = detect_hazardous_areas(enhanced_coords, steps)
    
    # Fetch accident-prone areas (with error handling)
    try:
        accident_zones = fetch_accident_data(enhanced_coords, gmaps)
    except Exception as e:
        print(f"Error fetching accident data: {e}")
        accident_zones = []

    def get_enhanced_pois(keyword, coords_sample):
        """Enhanced POI fetching with better sampling"""
        pois = []
        # Sample every 20th coordinate for better coverage
        for lat, lng in coords_sample[::20]:
            try:
                places = gmaps.places_nearby(
                    location=(lat, lng), 
                    radius=300, 
                    keyword=keyword
                )
                for place in places.get('results', []):
                    pois.append({
                        'name': place['name'],
                        'location': (
                            place['geometry']['location']['lat'],
                            place['geometry']['location']['lng']
                        ),
                        'type': keyword,
                        'rating': place.get('rating', 'N/A')
                    })
            except Exception as e:
                print(f"Error fetching POIs: {e}")
                continue
        return pois

    # Fetch POIs with enhanced sampling
    all_pois = []
    for keyword in ['hospital', 'police', 'fuel', 'mechanic', 'pharmacy']:
        pois = get_enhanced_pois(keyword, enhanced_coords)
        all_pois.extend(pois)

    # Create enhanced map
    m = folium.Map(location=source, zoom_start=13)
    
    # Add start and end markers
    folium.Marker(source, popup='Start', 
                  icon=folium.Icon(color='green', icon='flag', prefix='fa')).add_to(m)
    folium.Marker(destination, popup='End', 
                  icon=folium.Icon(color='black', icon='flag-checkered', prefix='fa')).add_to(m)
    
    # Add main route line
    folium.PolyLine(original_coords, color='blue', weight=5, opacity=0.8).add_to(m)

    # Enhanced marker styles
    marker_styles = {
        'hospital': {'color': 'red', 'icon': 'plus'},
        'police': {'color': 'blue', 'icon': 'shield'},
        'fuel': {'color': 'orange', 'icon': 'gas-pump'},
        'mechanic': {'color': 'purple', 'icon': 'wrench'},
        'pharmacy': {'color': 'green', 'icon': 'medkit'}
    }

    # Add POI markers
    for poi in all_pois:
        props = marker_styles.get(poi['type'], {'color': 'gray', 'icon': 'info-circle'})
        icon = folium.Icon(color=props['color'], icon=props['icon'], prefix='fa')
        popup_text = f"{poi['type'].capitalize()}: {poi['name']}"
        if poi.get('rating') != 'N/A':
            popup_text += f"\nRating: {poi['rating']}"
        
        folium.Marker(
            location=poi['location'],
            popup=popup_text,
            icon=icon
        ).add_to(m)

    # Add hazard markers
    hazard_colors = {'high': 'red', 'medium': 'orange', 'low': 'yellow'}
    for hazard in hazards:
        color = hazard_colors.get(hazard['severity'], 'red')
        
        if hazard['type'] == 'blind_turn':
            icon_name = 'exclamation-triangle'
        elif hazard['type'] == 'merge_point':
            icon_name = 'road'
        elif hazard['type'] == 'roundabout':
            icon_name = 'circle'
        else:
            icon_name = 'warning'
        
        popup_html = f"""
        <div style='font-family:Arial; font-size:13px; max-width:200px'>
            <b>⚠️ {hazard['type'].replace('_', ' ').title()}</b><br>
            <b>Severity:</b> {hazard['severity'].title()}<br>
            <b>Description:</b> {hazard['description']}<br>
        </div>
        """
        
        folium.Marker(
            location=hazard['location'],
            popup=popup_html,
            icon=folium.Icon(color=color, icon=icon_name, prefix='fa')
        ).add_to(m)

    # Add accident-prone area markers
    for accident in accident_zones:
        folium.Marker(
            location=accident['location'],
            popup=f"⚠️ Accident Prone Area: {accident['name']}",
            icon=folium.Icon(color='darkred', icon='car-crash', prefix='fa')
        ).add_to(m)

    # Add step-by-step navigation markers
    for step in steps:
        instruction = step['html_instructions']
        lat = step['end_location']['lat']
        lng = step['end_location']['lng']
        
        # Enhanced instruction analysis
        if "blind" in instruction.lower():
            icon_type = 'exclamation-triangle'
            color = 'red'
            title = "⚠️ Blind Spot Warning"
        elif "left" in instruction.lower():
            icon_type = 'arrow-left'
            color = 'blue'
            title = "Turn Left"
        elif "right" in instruction.lower():
            icon_type = 'arrow-right'
            color = 'green'
            title = "Turn Right"
        elif "u-turn" in instruction.lower():
            icon_type = 'undo'
            color = 'purple'
            title = "U-turn"
        else:
            icon_type = 'arrow-up'
            color = 'gray'
            title = "Continue Straight"

        popup_html = f"""
        <div style='font-family:Arial; font-size:13px; max-width:250px'>
            <b>{title}</b><br>
            {instruction}<br>
            <i>Distance: {step['distance']['text']}</i><br>
            <i>Duration: {step['duration']['text']}</i>
        </div>
        """
        
        folium.Marker(
            location=(lat, lng),
            popup=popup_html,
            icon=folium.Icon(color=color, icon=icon_type, prefix='fa')
        ).add_to(m)

    # Enhanced legend
    legend_html = """
    {% macro html(this, kwargs) %}
    <div style="
        position: fixed;
        bottom: 20px;
        left: 20px;
        width: 280px;
        background-color: white;
        border: 2px solid grey;
        border-radius: 8px;
        z-index: 9999;
        padding: 15px;
        font-size: 12px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    ">
        <h4 style="margin-top:0;">Legend</h4>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px;">
            <div><i class="fa fa-plus fa-lg" style="color:red"></i> Hospital</div>
            <div><i class="fa fa-shield fa-lg" style="color:blue"></i> Police</div>
            <div><i class="fa fa-gas-pump fa-lg" style="color:orange"></i> Fuel</div>
            <div><i class="fa fa-wrench fa-lg" style="color:purple"></i> Mechanic</div>
            <div><i class="fa fa-medkit fa-lg" style="color:green"></i> Pharmacy</div>
            <div><i class="fa fa-exclamation-triangle fa-lg" style="color:red"></i> High Hazard</div>
            <div><i class="fa fa-exclamation-triangle fa-lg" style="color:orange"></i> Medium Hazard</div>
            <div><i class="fa fa-car-crash fa-lg" style="color:darkred"></i> Accident Prone</div>
            <div><i class="fa fa-arrow-left fa-lg" style="color:blue"></i> Left Turn</div>
            <div><i class="fa fa-arrow-right fa-lg" style="color:green"></i> Right Turn</div>
            <div><i class="fa fa-undo fa-lg" style="color:purple"></i> U-turn</div>
            <div><i class="fa fa-flag fa-lg" style="color:green"></i> Start</div>
        </div>
    </div>
    {% endmacro %}
    """
    legend = MacroElement()
    legend._template = Template(legend_html)
    m.get_root().add_child(legend)

    # Save enhanced map
    unique_map_id = uuid4().hex
    html_name = f"route_map_{unique_map_id}.html"
    m.save(f"templates/{html_name}")

    # Enhanced analysis statistics
    total_turns = sum("turn" in s['html_instructions'].lower() for s in steps)
    blind_turns = len([h for h in hazards if h['type'] == 'blind_turn'])
    high_hazards = len([h for h in hazards if h['severity'] == 'high'])
    
    return render_template("route_analysis.html",
                           mode=session['vehicle'],
                           turns=total_turns,
                           blind_turns=blind_turns,
                           high_hazards=high_hazards,
                           poi_count=len(all_pois),
                           accident_zones=len(accident_zones),
                           total_hazards=len(hazards),
                           coordinate_density=len(enhanced_coords),
                           html_file=html_name)

@app.route('/view_map/<filename>')
def view_map(filename):
    path = os.path.join("templates", filename)
    if not os.path.exists(path):
        return "Map file not found", 404
    response = make_response(render_template(filename))
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.route('/download/<filename>')
def download_map(filename):
    return send_from_directory(directory='templates', path=filename, as_attachment=True)

@app.route('/preview/<filename>')
def view_preview(filename):
    path = os.path.join("templates", filename)
    if not os.path.exists(path):
        return "Preview not found.", 404
    response = make_response(render_template(filename))
    response.headers['Cache-Control'] = 'no-store'
    return response

if __name__ == '__main__':
    if not os.path.exists("templates"):
        os.makedirs("templates")
    app.run(debug=True)
