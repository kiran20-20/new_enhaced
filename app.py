from flask import Flask, render_template, request, send_file
import googlemaps
import folium
import os
from datetime import datetime
from uuid import uuid4
from fpdf import FPDF
import json

app = Flask(__name__)

# Configure Google Maps API
gmaps = googlemaps.Client(key="YOUR_GOOGLE_MAPS_API_KEY")  # <-- Replace with your actual key

# Directory to store temporary files
TEMP_DIR = "temp"
os.makedirs(TEMP_DIR, exist_ok=True)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        start_location = request.form["start"]
        end_location = request.form["end"]

        # Get directions
        directions = gmaps.directions(start_location, end_location, mode="driving", departure_time=datetime.now())
        if not directions:
            return render_template("index.html", error="Could not fetch directions")

        route = directions[0]['legs'][0]
        start_coords = (route['start_location']['lat'], route['start_location']['lng'])
        end_coords = (route['end_location']['lat'], route['end_location']['lng'])

        # Decode full path polyline
        path = googlemaps.convert.decode_polyline(directions[0]['overview_polyline']['points'])

        # Perform complex route analysis
        analysis_results = {
            "start": start_coords,
            "end": end_coords,
            "distance": route['distance']['text'],
            "duration": route['duration']['text'],
            "high_risk": detect_high_risk_zones(path),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Save route and analysis to PDF
        pdf_path = generate_pdf_report(start_location, end_location, analysis_results)

        # Render folium map
        map_path = render_map(path, start_coords, end_coords)

        return render_template(
            "result.html",
            analysis=analysis_results,
            map_file=map_path,
            pdf_file=os.path.basename(pdf_path)
        )

    return render_template("index.html")

@app.route("/download/<filename>")
def download(filename):
    return send_file(os.path.join(TEMP_DIR, filename), as_attachment=True)

# ----------------- Utility Functions -------------------

def detect_high_risk_zones(path):
    # Dummy logic — improve using traffic, crime data, accident zones etc.
    risky_zones = []
    for i, coord in enumerate(path):
        if i % 15 == 0:  # Every 15th point marked as "high risk" for simulation
            risky_zones.append({"lat": coord['lat'], "lng": coord['lng']})
    return risky_zones

def render_map(path, start, end):
    m = folium.Map(location=start, zoom_start=12)

    # Add route polyline
    folium.PolyLine([(p['lat'], p['lng']) for p in path], color="blue", weight=5).add_to(m)

    # Add start and end markers
    folium.Marker(start, tooltip="Start", icon=folium.Icon(color='green')).add_to(m)
    folium.Marker(end, tooltip="End", icon=folium.Icon(color='red')).add_to(m)

    # Add high-risk markers
    for zone in detect_high_risk_zones(path):
        folium.CircleMarker(
            location=(zone['lat'], zone['lng']),
            radius=6,
            color='red',
            fill=True,
            fill_opacity=0.7,
            tooltip="High Risk Zone"
        ).add_to(m)

    file_name = f"{uuid4().hex}.html"
    full_path = os.path.join(TEMP_DIR, file_name)
    m.save(full_path)
    return file_name

def generate_pdf_report(start, end, analysis):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.cell(200, 10, txt="Route Analysis Report", ln=True, align="C")
    pdf.ln(10)
    pdf.cell(200, 10, txt=f"Start Location: {start}", ln=True)
    pdf.cell(200, 10, txt=f"End Location: {end}", ln=True)
    pdf.cell(200, 10, txt=f"Distance: {analysis['distance']}", ln=True)
    pdf.cell(200, 10, txt=f"Duration: {analysis['duration']}", ln=True)
    pdf.cell(200, 10, txt=f"Time of Analysis: {analysis['timestamp']}", ln=True)
    pdf.ln(5)

    pdf.cell(200, 10, txt="High Risk Zones (Lat, Lng):", ln=True)
    for zone in analysis['high_risk']:
        pdf.cell(200, 10, txt=f"{zone['lat']:.4f}, {zone['lng']:.4f}", ln=True)

    file_name = f"route_report_{uuid4().hex}.pdf"
    pdf_path = os.path.join(TEMP_DIR, file_name)
    pdf.output(pdf_path)
    return pdf_path

# ------------------ (Optional) OpenAI AI-Based Insights ---------------------
# Future integration to generate summaries from OpenAI:
# def generate_insight(summary_prompt):
#     import openai
#     openai.api_key = "YOUR_API_KEY"
#     response = openai.ChatCompletion.create(
#         model="gpt-4",
#         messages=[{"role": "system", "content": "You are a route analysis assistant."},
#                   {"role": "user", "content": summary_prompt}]
#     )
#     return response['choices'][0]['message']['content']

# ----------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
