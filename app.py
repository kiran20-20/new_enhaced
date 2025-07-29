from flask import Flask, request, send_from_directory, make_response, render_template, redirect, url_for
import googlemaps
import polyline
import folium
from datetime import datetime
import os
import pandas as pd
import json
import glob
from uuid import uuid4
from geopy.distance import geodesic

app = Flask(__name__)
gmaps = googlemaps.Client(key='YOUR_API_KEY')  # Replace with your API key

# Ensure templates directory exists
TEMPLATE_DIR = "templates"
os.makedirs(TEMPLATE_DIR, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    source = request.form['source']
    destination = request.form['destination']
    now = datetime.now()

    try:
        directions = gmaps.directions(source, destination, mode="driving", departure_time=now)
        if not directions:
            return "No route found. Please check the input.", 404

        route = directions[0]['legs'][0]
        distance = route['distance']['text']
        duration = route['duration']['text']
        start_address = route['start_address']
        end_address = route['end_address']
        steps = route['steps']
        path = polyline.decode(directions[0]['overview_polyline']['points'])

        folium_map = folium.Map(location=path[0], zoom_start=13)
        folium.PolyLine(path, color="blue", weight=5).add_to(folium_map)
        folium.Marker(path[0], tooltip="Start", icon=folium.Icon(color='green')).add_to(folium_map)
        folium.Marker(path[-1], tooltip="End", icon=folium.Icon(color='red')).add_to(folium_map)

        map_filename = f"route_map_{uuid4().hex}.html"
        map_path = os.path.join(TEMPLATE_DIR, map_filename)
        folium_map.save(map_path)

        return render_template("result.html", source=start_address, destination=end_address,
                               distance=distance, duration=duration, map_file=map_filename)

    except Exception as e:
        return f"Error occurred: {str(e)}", 500

@app.route('/view_map/<filename>')
def view_map(filename):
    path = os.path.join(TEMPLATE_DIR, filename)
    if not os.path.exists(path):
        return "Map file not found", 404
    return send_from_directory(TEMPLATE_DIR, filename)

@app.route('/preview/<filename>')
def view_preview(filename):
    path = os.path.join(TEMPLATE_DIR, filename)
    if not os.path.exists(path):
        return "Preview not found", 404
    return send_from_directory(TEMPLATE_DIR, filename)

@app.route('/download/<filename>')
def download_map(filename):
    return send_from_directory(directory=TEMPLATE_DIR, path=filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
