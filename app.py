from flask import Flask, render_template, request, session, redirect, url_for, send_file, make_response
import googlemaps
import polyline
import folium
from datetime import datetime
from flask_session import Session
from branca.element import Template, MacroElement
import os
import pandas as pd
import json
from uuid import uuid4
import numpy as np
from geopy.distance import geodesic
import requests
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

gmaps = googlemaps.Client(key='YOUR_GOOGLE_MAPS_API_KEY')
WEATHER_API_KEY = 'YOUR_OPENWEATHERMAP_API_KEY'
OPENAI_API_KEY = 'YOUR_OPENAI_API_KEY'

# Load landmark data
landmarks_df = pd.read_excel('IOCL_Landmark_Details.xlsx')

# Utility to get weather info
def get_weather(lat, lon):
    url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
    res = requests.get(url)
    return res.json() if res.status_code == 200 else {}

# Utility to get traffic info (mocked or add live integration)
def get_traffic_info(origin, destination):
    directions = gmaps.directions(origin, destination, departure_time='now', traffic_model='best_guess')
    return directions

# Generate PDF summary
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 10, 'Route Analysis Report', 0, 1, 'C')

    def add_route_info(self, summary):
        self.set_font('Arial', '', 10)
        self.multi_cell(0, 10, summary)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        source = request.form['source']
        destination = request.form['destination']

        directions = gmaps.directions(source, destination)
        points = polyline.decode(directions[0]['overview_polyline']['points'])

        m = folium.Map(location=points[0], zoom_start=8)
        folium.PolyLine(points, color="blue", weight=2.5, opacity=1).add_to(m)

        # High-risk zone detection (mock logic)
        high_risk_points = [pt for pt in points if np.random.rand() < 0.05]
        for pt in high_risk_points:
            folium.Marker(pt, icon=folium.Icon(color='red'), tooltip="High Risk Zone").add_to(m)

        # Weather info along the way
        weather_report = []
        for pt in points[::int(len(points)/5)+1]:
            weather = get_weather(pt[0], pt[1])
            weather_report.append(weather.get('weather', [{}])[0].get('description', 'N/A'))

        # Generate summary
        summary = f"Source: {source}\nDestination: {destination}\nHigh-risk zones: {len(high_risk_points)}\nWeather: {weather_report}"

        # Save to session
        session['summary'] = summary

        # Save map
        map_path = f"static/maps/{uuid4().hex}.html"
        m.save(map_path)

        return render_template('preview.html', map_path=map_path, summary=summary)

    return render_template('index.html')

@app.route('/generate-pdf')
def generate_pdf():
    summary = session.get('summary', 'No summary available')
    pdf = PDFReport()
    pdf.add_page()
    pdf.add_route_info(summary)
    filename = f"/mnt/data/route_report_{uuid4().hex}.pdf"
    pdf.output(filename)
    return send_file(filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
