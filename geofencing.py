#!/usr/bin/env python3
"""
Enhanced BlueNet - Maritime Boundary Crossing Alert System
Focuses on emergency calling when crossing national boundaries with fake GPS testing
"""

import os
import json
import time
import threading
import argparse
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional
import sqlite3
import uuid
import random
import math

from flask import Flask, request, jsonify, render_template_string

# Geometry & distance calculations
try:
    from shapely.geometry import shape, Point, LineString
    from geopy.distance import geodesic
    SHAPELY_AVAILABLE = True
except ImportError:
    print("Installing required packages: pip install shapely geopy twilio flask")
    SHAPELY_AVAILABLE = False

# Twilio for emergency calling
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    print("Twilio not available. Install with: pip install twilio")
    TWILIO_AVAILABLE = False

# Configuration
class Config:
    # Boundary detection - more aggressive for testing
    BOUNDARY_BUFFER_METERS = 500       # 500m buffer for boundary crossing detection
    HYSTERESIS_COUNT = 1               # Immediate response for testing
    GPS_ACCURACY_MAX = 100
    
    # Emergency calling - reduced cooldowns for testing
    CALL_COOLDOWN_SECONDS = 30         # 30 seconds between calls for testing
    MAX_EMERGENCY_CALLS = 3            # Maximum calls per crossing event
    
    # Twilio credentials
    TWILIO_ACCOUNT_SID = "AC8c30bcb8589dd88c706dea01e2d25bc9"
    TWILIO_AUTH_TOKEN = "d55d15fd58c739594e425c4552bfcfd7"
    TWILIO_PHONE_NUMBER = "+19205251279"
    RECIPIENT_PHONE_NUMBER = "+918146795946"
    
    # Server
    SERVER_PORT = 5000
    DATABASE_FILE = "bluenet_boundary_logs.db"
    
    # Enhanced Test Mode with Boundary Crossing Focus
    TEST_MODE = False
    TEST_INTERVAL = 2  # Faster updates for testing
    
    # Realistic test coordinates focusing on boundary crossing
    TEST_SCENARIOS = [
        {"name": "inside_indian_waters", "lat": 22.5, "lon": 69.0, "inside_boundary": True, "duration": 8},
        {"name": "approaching_boundary", "lat": 23.95, "lon": 68.05, "inside_boundary": True, "duration": 6},
        {"name": "at_boundary_line", "lat": 24.0, "lon": 68.0, "inside_boundary": True, "duration": 4},
        {"name": "CROSSED_BOUNDARY", "lat": 24.05, "lon": 67.95, "inside_boundary": False, "duration": 8},
        {"name": "deep_in_pakistan_waters", "lat": 24.2, "lon": 67.8, "inside_boundary": False, "duration": 6},
        {"name": "returning_to_boundary", "lat": 24.0, "lon": 68.0, "inside_boundary": True, "duration": 4},
        {"name": "back_in_indian_waters", "lat": 23.8, "lon": 68.2, "inside_boundary": True, "duration": 10}
    ]

def init_database():
    """Initialize database with focus on boundary crossings"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    
    cursor.execute('PRAGMA journal_mode=WAL;')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS boundary_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            inside_boundary BOOLEAN NOT NULL,
            boundary_crossed BOOLEAN DEFAULT 0,
            distance_to_boundary REAL,
            session_id TEXT,
            alert_triggered BOOLEAN DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emergency_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            call_type TEXT NOT NULL,
            message TEXT NOT NULL,
            recipient TEXT,
            call_sid TEXT,
            success BOOLEAN,
            crossing_event_id INTEGER,
            session_id TEXT
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_boundary_timestamp ON boundary_events(timestamp);')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON emergency_calls(timestamp);')
    
    conn.commit()
    conn.close()

class BoundaryCrossingEngine:
    """Enhanced engine focused on boundary crossing detection"""
    
    def _init_(self, india_polygon, boundary_lines: List[LineString]):
        self.india_polygon = india_polygon
        self.boundary_lines = boundary_lines
        self.session_id = str(uuid.uuid4())[:8]
        
        # State tracking
        self.current_inside_boundary = True
        self.last_inside_boundary = True
        self.boundary_crossings = 0
        self.last_call_time = None
        self.calls_for_current_crossing = 0
        
        print(f"🛡 Boundary Crossing Engine initialized - Session: {self.session_id}")
    
    def process_gps(self, lat: float, lon: float, accuracy: float) -> Dict[str, Any]:
        """Process GPS and detect boundary crossings"""
        current_time = time.time()
        
        # Check if position is inside Indian waters
        pt = Point(lon, lat)
        inside_boundary = self.india_polygon.contains(pt)
        
        # Calculate distance to boundary
        distance_to_boundary = self._calculate_boundary_distance(lat, lon)
        
        # Detect boundary crossing
        boundary_crossed = False
        crossing_direction = None
        
        if inside_boundary != self.last_inside_boundary:
            boundary_crossed = True
            self.boundary_crossings += 1
            
            if inside_boundary:
                crossing_direction = "ENTERED_INDIAN_WATERS"
                self.calls_for_current_crossing = 0  # Reset call count when entering
            else:
                crossing_direction = "EXITED_INDIAN_WATERS"
                self.calls_for_current_crossing = 0  # Reset call count for new violation
        
        # Log the event
        event_id = self._log_boundary_event(
            lat, lon, inside_boundary, boundary_crossed, 
            distance_to_boundary, current_time
        )
        
        # Update state
        self.last_inside_boundary = inside_boundary
        self.current_inside_boundary = inside_boundary
        
        result = {
            "session_id": self.session_id,
            "latitude": lat,
            "longitude": lon,
            "inside_boundary": inside_boundary,
            "boundary_crossed": boundary_crossed,
            "crossing_direction": crossing_direction,
            "distance_to_boundary": distance_to_boundary,
            "total_crossings": self.boundary_crossings,
            "requires_emergency_call": False,
            "event_id": event_id
        }
        
        # Determine if emergency call is needed
        if boundary_crossed and not inside_boundary:  # Crossed OUT of Indian waters
            if self._should_make_emergency_call(current_time):
                result["requires_emergency_call"] = True
                result["call_reason"] = "BOUNDARY_VIOLATION"
        
        return result
    
    def _calculate_boundary_distance(self, lat: float, lon: float) -> float:
        """Calculate minimum distance to boundary lines"""
        min_distance = float("inf")
        
        for line in self.boundary_lines:
            # Calculate distance to line using geodesic distance
            for i in range(len(line.coords) - 1):
                p1 = line.coords[i]
                p2 = line.coords[i + 1]
                
                # Distance to line segment
                dist1 = geodesic((lat, lon), (p1[1], p1[0])).meters
                dist2 = geodesic((lat, lon), (p2[1], p2[0])).meters
                
                min_distance = min(min_distance, dist1, dist2)
        
        return min_distance if min_distance != float("inf") else 0
    
    def _should_make_emergency_call(self, current_time: float) -> bool:
        """Determine if emergency call should be made"""
        # Don't exceed maximum calls per crossing
        if self.calls_for_current_crossing >= Config.MAX_EMERGENCY_CALLS:
            return False
        
        # Check cooldown period
        if self.last_call_time and (current_time - self.last_call_time) < Config.CALL_COOLDOWN_SECONDS:
            return False
        
        return True
    
    def _log_boundary_event(self, lat: float, lon: float, inside_boundary: bool, 
                           boundary_crossed: bool, distance: float, timestamp: float) -> int:
        """Log boundary event to database"""
        try:
            conn = sqlite3.connect(Config.DATABASE_FILE)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO boundary_events 
                (timestamp, latitude, longitude, inside_boundary, boundary_crossed, 
                 distance_to_boundary, session_id, alert_triggered)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                int(timestamp), lat, lon, inside_boundary, boundary_crossed,
                distance, self.session_id, boundary_crossed and not inside_boundary
            ))
            
            event_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            # Log to console
            status = "INSIDE" if inside_boundary else "OUTSIDE"
            if boundary_crossed:
                direction = "ENTERED" if inside_boundary else "EXITED"
                print(f"🚨 BOUNDARY {direction}: {status} Indian waters - Distance: {distance:.0f}m")
            else:
                print(f"📍 Position: {status} Indian waters - Distance: {distance:.0f}m")
            
            return event_id
            
        except Exception as e:
            print(f"Database logging error: {e}")
            return 0

class EmergencyCallSystem:
    """Enhanced emergency calling system for boundary violations"""
    
    def _init_(self):
        self.twilio_client = None
        if TWILIO_AVAILABLE:
            try:
                self.twilio_client = TwilioClient(Config.TWILIO_ACCOUNT_SID, Config.TWILIO_AUTH_TOKEN)
                print("📞 Emergency calling system ready")
            except Exception as e:
                print(f"❌ Twilio initialization failed: {e}")
                self.twilio_client = None
        else:
            print("📞 Emergency calling system in simulation mode")
    
    def make_boundary_violation_call(self, lat: float, lon: float, distance: float, 
                                   session_id: str, event_id: int) -> bool:
        """Make emergency call for boundary violation"""
        
        message = self._create_boundary_alert_message(lat, lon, distance, session_id)
        
        if not self.twilio_client:
            return self._simulate_emergency_call(message, session_id, event_id)
        
        try:
            # Create TwiML for emergency call
            twiml = f"""
            <Response>
                <Say voice="alice" rate="slow">
                    URGENT MARITIME SECURITY ALERT from BlueNet System.
                </Say>
                <Pause length="1"/>
                <Say voice="alice">
                    A vessel has crossed the national maritime boundary into restricted waters.
                </Say>
                <Pause length="1"/>
                <Say voice="alice">
                    Current position: Latitude {lat:.4f}, Longitude {lon:.4f}.
                    Distance from boundary: {distance:.0f} meters.
                </Say>
                <Pause length="1"/>
                <Say voice="alice">
                    This is a CRITICAL security alert. Immediate response required.
                    Session ID: {session_id}.
                </Say>
                <Pause length="1"/>
                <Say voice="alice">
                    Please acknowledge this alert and take appropriate action.
                    This message will repeat once.
                </Say>
                <Pause length="2"/>
                <Say voice="alice" rate="slow">
                    Repeating: Vessel has crossed maritime boundary. 
                    Position: Latitude {lat:.4f}, Longitude {lon:.4f}.
                    Immediate response required.
                </Say>
            </Response>
            """
            
            call = self.twilio_client.calls.create(
                twiml=twiml,
                to=Config.RECIPIENT_PHONE_NUMBER,
                from_=Config.TWILIO_PHONE_NUMBER
            )
            
            self._log_emergency_call("BOUNDARY_VIOLATION", message, True, call.sid, event_id, session_id)
            print(f"📞 Emergency call placed successfully - Call SID: {call.sid}")
            return True
            
        except Exception as e:
            self._log_emergency_call("BOUNDARY_VIOLATION", message, False, None, event_id, session_id)
            print(f"❌ Emergency call failed: {e}")
            return False
    
    def _simulate_emergency_call(self, message: str, session_id: str, event_id: int) -> bool:
        """Simulate emergency call for testing"""
        print("\n" + "="*60)
        print("📞 EMERGENCY CALL SIMULATION")
        print("="*60)
        print(f"TO: {Config.RECIPIENT_PHONE_NUMBER}")
        print(f"FROM: BlueNet Maritime Security System")
        print("\nMESSAGE:")
        print(message)
        print("="*60)
        
        self._log_emergency_call("BOUNDARY_VIOLATION", message, True, "SIM_CALL", event_id, session_id)
        return True
    
    def _create_boundary_alert_message(self, lat: float, lon: float, distance: float, session_id: str) -> str:
        """Create emergency alert message"""
        return (
            f"🚨 MARITIME BOUNDARY VIOLATION ALERT 🚨\n\n"
            f"A vessel has crossed into restricted waters!\n"
            f"Position: {lat:.6f}°N, {lon:.6f}°E\n"
            f"Distance from boundary: {distance:.0f} meters\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"Session: {session_id}\n\n"
            f"IMMEDIATE RESPONSE REQUIRED"
        )
    
    def _log_emergency_call(self, call_type: str, message: str, success: bool, 
                          call_sid: str, event_id: int, session_id: str):
        """Log emergency call to database"""
        try:
            conn = sqlite3.connect(Config.DATABASE_FILE)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO emergency_calls 
                (timestamp, call_type, message, recipient, call_sid, success, 
                 crossing_event_id, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                int(time.time()), call_type, message, Config.RECIPIENT_PHONE_NUMBER,
                call_sid, success, event_id, session_id
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"Call logging error: {e}")

class BoundaryTestingSystem:
    """System for testing boundary crossing with fake GPS coordinates"""
    
    def _init_(self, engine: BoundaryCrossingEngine, emergency_system: EmergencyCallSystem):
        self.engine = engine
        self.emergency_system = emergency_system
        self.running = False
        self.thread = None
        self.scenario_index = 0
        self.scenario_timer = 0
        
    def start_testing(self):
        """Start boundary crossing test with fake coordinates"""
        if self.running:
            return False
        
        self.running = True
        self.thread = threading.Thread(target=self._run_test_scenarios, daemon=True)
        self.thread.start()
        print("\n🧪 Boundary crossing test started with fake GPS coordinates")
        return True
    
    def stop_testing(self):
        """Stop boundary testing"""
        self.running = False
        if self.thread:
            self.thread.join()
        print("🛑 Boundary testing stopped")
    
    def _run_test_scenarios(self):
        """Run through test scenarios with fake GPS coordinates"""
        print("\n📍 Starting boundary crossing simulation...")
        
        while self.running:
            try:
                scenario = Config.TEST_SCENARIOS[self.scenario_index]
                
                # Add realistic GPS noise
                lat_noise = random.uniform(-0.0005, 0.0005)  # ~50m variation
                lon_noise = random.uniform(-0.0005, 0.0005)
                
                lat = scenario["lat"] + lat_noise
                lon = scenario["lon"] + lon_noise
                accuracy = random.uniform(3, 15)  # Good GPS accuracy
                
                print(f"\n🎯 Scenario: {scenario['name']}")
                print(f"📍 Fake GPS: {lat:.6f}°, {lon:.6f}°")
                print(f"🌊 Expected: {'Inside' if scenario['inside_boundary'] else 'Outside'} Indian waters")
                
                # Process fake GPS coordinates
                result = self.engine.process_gps(lat, lon, accuracy)
                
                # Handle emergency calls for boundary violations
                if result.get("requires_emergency_call"):
                    print("🚨 Triggering emergency call for boundary violation!")
                    
                    call_success = self.emergency_system.make_boundary_violation_call(
                        result["latitude"], result["longitude"], 
                        result["distance_to_boundary"], result["session_id"], 
                        result["event_id"]
                    )
                    
                    if call_success:
                        self.engine.last_call_time = time.time()
                        self.engine.calls_for_current_crossing += 1
                
                # Display results
                self._display_test_results(result, scenario)
                
                # Update scenario timing
                self.scenario_timer += Config.TEST_INTERVAL
                if self.scenario_timer >= scenario["duration"]:
                    self.scenario_timer = 0
                    self.scenario_index = (self.scenario_index + 1) % len(Config.TEST_SCENARIOS)
                    print(f"\n➡ Moving to next scenario...")
                
                time.sleep(Config.TEST_INTERVAL)
                
            except Exception as e:
                print(f"❌ Test error: {e}")
                time.sleep(Config.TEST_INTERVAL)
    
    def _display_test_results(self, result: Dict, scenario: Dict):
        """Display test results"""
        status = "✅ INSIDE" if result["inside_boundary"] else "🚨 OUTSIDE"
        expected = "✅ CORRECT" if result["inside_boundary"] == scenario["inside_boundary"] else "❌ UNEXPECTED"
        
        print(f"Status: {status} Indian waters - {expected}")
        print(f"Distance to boundary: {result['distance_to_boundary']:.0f}m")
        
        if result["boundary_crossed"]:
            direction = result["crossing_direction"]
            print(f"🚨 BOUNDARY CROSSED: {direction}")
            
        if result["requires_emergency_call"]:
            print("📞 Emergency call will be triggered!")

# Flask Web Application
app = Flask(__name__)
ENGINE = None
EMERGENCY_SYSTEM = EmergencyCallSystem()
TEST_SYSTEM = None

# Enhanced Dashboard HTML
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>BlueNet Boundary Crossing Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="3">
    <style>
        body { font-family: Arial, sans-serif; background: #0a0a0a; color: white; margin: 0; }
        .header { background: linear-gradient(45deg, #1e3c72, #2a5298); padding: 20px; text-align: center; }
        .alert { background: #f44336; color: white; padding: 15px; text-align: center; font-weight: bold; 
                 animation: pulse 1s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .card { background: #1a1a1a; border-radius: 10px; padding: 20px; margin: 10px 0; }
        .card-inside { border-left: 4px solid #4caf50; }
        .card-outside { border-left: 4px solid #f44336; animation: danger 1s infinite; }
        @keyframes danger { 0%, 100% { border-left-color: #f44336; } 50% { border-left-color: #ff5722; } }
        .metric { font-size: 24px; font-weight: bold; margin: 10px 0; }
        .controls { text-align: center; margin: 20px 0; }
        .btn { background: #2196F3; color: white; border: none; padding: 10px 20px; 
               border-radius: 5px; cursor: pointer; margin: 5px; }
        .btn-danger { background: #f44336; }
        .btn-success { background: #4caf50; }
        .coordinates { font-family: monospace; background: #333; padding: 10px; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🌊 BlueNet Boundary Crossing Monitor</h1>
        <p>Real-time Maritime Border Security System</p>
    </div>
    
    {% if not status.inside_boundary %}
    <div class="alert">
        🚨 BOUNDARY VIOLATION: Vessel is OUTSIDE Indian territorial waters! 🚨
    </div>
    {% endif %}
    
    <div class="container">
        <div class="controls">
            <button class="btn btn-success" onclick="fetch('/start-boundary-test')">🧪 Start Boundary Test</button>
            <button class="btn btn-danger" onclick="fetch('/stop-boundary-test')">🛑 Stop Test</button>
            <button class="btn" onclick="fetch('/test-emergency-call')">📞 Test Emergency Call</button>
        </div>
        
        <div class="status-grid">
            <div class="card {{ 'card-inside' if status.inside_boundary else 'card-outside' }}">
                <h3>Boundary Status</h3>
                <div class="metric">{{ "INSIDE" if status.inside_boundary else "OUTSIDE" }}</div>
                <p>{{ "✅ Within Indian waters" if status.inside_boundary else "🚨 In restricted waters" }}</p>
                <p>Distance: {{ "%.0f"|format(status.distance_to_boundary) }} meters</p>
                <p>Total Crossings: {{ status.total_crossings }}</p>
            </div>
            
            <div class="card">
                <h3>Current Position</h3>
                <div class="coordinates">
                    Lat: {{ "%.6f"|format(status.latitude) }}<br>
                    Lon: {{ "%.6f"|format(status.longitude) }}
                </div>
                <p>Session: {{ status.session_id }}</p>
                <p>Last Update: {{ status.last_update }}</p>
            </div>
            
            <div class="card">
                <h3>Emergency System</h3>
                <div class="metric">{{ "READY" if emergency_ready else "LIMITED" }}</div>
                <p>Calls Made: {{ emergency_calls }}</p>
                <p>Success Rate: {{ success_rate }}%</p>
                <p>Status: {{ "📞 Active" if emergency_ready else "📞 Simulated" }}</p>
            </div>
        </div>
        
        <div class="card">
            <h3>Recent Boundary Events</h3>
            <div style="max-height: 300px; overflow-y: auto;">
                {% for event in recent_events %}
                <div style="padding: 5px; border-bottom: 1px solid #333;">
                    <strong>{{ event.timestamp }}</strong> - 
                    <span style="color: {{ '#4caf50' if event.inside_boundary else '#f44336' }}">
                        {{ "INSIDE" if event.inside_boundary else "OUTSIDE" }}
                    </span>
                    {% if event.boundary_crossed %}
                    <strong style="color: #ff5722;"> - BOUNDARY CROSSED!</strong>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
        </div>
    </div>
</body>
</html>
'''

@app.route("/")
def dashboard():
    """Enhanced dashboard for boundary monitoring"""
    try:
        conn = sqlite3.connect(Config.DATABASE_FILE)
        cursor = conn.cursor()
        
        # Get latest boundary event
        cursor.execute('SELECT * FROM boundary_events ORDER BY timestamp DESC LIMIT 1')
        latest_event = cursor.fetchone()
        
        # Get total crossings and emergency calls
        cursor.execute('SELECT COUNT(*) FROM boundary_events WHERE boundary_crossed = 1')
        total_crossings = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM emergency_calls')
        total_calls = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM emergency_calls WHERE success = 1')
        successful_calls = cursor.fetchone()[0]
        
        # Get recent events
        cursor.execute('''SELECT timestamp, inside_boundary, boundary_crossed 
                         FROM boundary_events ORDER BY timestamp DESC LIMIT 10''')
        recent_events = []
        for row in cursor.fetchall():
            recent_events.append({
                'timestamp': datetime.fromtimestamp(row[0]).strftime('%H:%M:%S'),
                'inside_boundary': bool(row[1]),
                'boundary_crossed': bool(row[2])
            })
        
        conn.close()
        
        if latest_event:
            status = {
                "inside_boundary": bool(latest_event[3]),
                "latitude": latest_event[1],
                "longitude": latest_event[2],
                "distance_to_boundary": latest_event[5] or 0,
                "total_crossings": total_crossings,
                "session_id": latest_event[6],
                "last_update": datetime.fromtimestamp(latest_event[0]).strftime('%H:%M:%S')
            }
        else:
            status = {
                "inside_boundary": True,
                "latitude": 0,
                "longitude": 0,
                "distance_to_boundary": 0,
                "total_crossings": 0,
                "session_id": "no-data",
                "last_update": "Never"
            }
        
        success_rate = int((successful_calls / total_calls * 100)) if total_calls > 0 else 100
        
        return render_template_string(DASHBOARD_HTML, 
                                    status=status,
                                    emergency_ready=EMERGENCY_SYSTEM.twilio_client is not None,
                                    emergency_calls=total_calls,
                                    success_rate=success_rate,
                                    recent_events=recent_events)
    except Exception as e:
        return f"Dashboard error: {e}", 500

@app.route("/gps", methods=["GET", "POST"])
def gps_endpoint():
    """Process GPS coordinates and check for boundary crossings"""
    try:
        lat = float(request.args.get("lat") or request.form.get("lat"))
        lon = float(request.args.get("lon") or request.form.get("lon"))
        accuracy = float(request.args.get("accuracy", 10))
        
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return jsonify({"error": "Invalid coordinates"}), 400
        
        # Process GPS through boundary engine
        result = ENGINE.process_gps(lat, lon, accuracy)
        
        # Handle emergency calls
        if result.get("requires_emergency_call"):
            call_success = EMERGENCY_SYSTEM.make_boundary_violation_call(
                result["latitude"], result["longitude"],
                result["distance_to_boundary"], result["session_id"],
                result["event_id"]
            )
            
            if call_success:
                ENGINE.last_call_time = time.time()
                ENGINE.calls_for_current_crossing += 1
                result["emergency_call_made"] = True
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/start-boundary-test")
def start_boundary_test():
    """Start boundary crossing test with fake coordinates"""
    try:
        success = TEST_SYSTEM.start_testing()
        Config.TEST_MODE = success
        
        return jsonify({
            "status": "test_started" if success else "already_running",
            "message": "Boundary crossing test started with fake GPS coordinates",
            "scenarios": len(Config.TEST_SCENARIOS)
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/stop-boundary-test")
def stop_boundary_test():
    """Stop boundary crossing test"""
    try:
        TEST_SYSTEM.stop_testing()
        Config.TEST_MODE = False
        
        return jsonify({
            "status": "test_stopped",
            "message": "Boundary crossing test stopped"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/test-emergency-call")
def test_emergency_call():
    """Test emergency call system"""
    try:
        # Use current position or fake coordinates
        lat, lon = 24.05, 67.95  # Outside boundary coordinates
        distance = 50  # 50m outside boundary
        
        success = EMERGENCY_SYSTEM.make_boundary_violation_call(
            lat, lon, distance, ENGINE.session_id, 0
        )
        
        return jsonify({
            "status": "call_test_completed",
            "success": success,
            "message": "Emergency call test completed",
            "coordinates": f"{lat}, {lon}"
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/boundary-status")
def boundary_status():
    """Get current boundary crossing status"""
    try:
        conn = sqlite3.connect(Config.DATABASE_FILE)
        cursor = conn.cursor()
        
        # Get latest status
        cursor.execute('SELECT * FROM boundary_events ORDER BY timestamp DESC LIMIT 1')
        latest_event = cursor.fetchone()
        
        # Get statistics
        cursor.execute('SELECT COUNT(*) FROM boundary_events WHERE boundary_crossed = 1')
        total_crossings = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM emergency_calls WHERE success = 1')
        successful_calls = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM boundary_events WHERE inside_boundary = 0')
        violations = cursor.fetchone()[0]
        
        conn.close()
        
        status = {
            "system_active": True,
            "test_mode": Config.TEST_MODE,
            "current_session": ENGINE.session_id if ENGINE else None,
            "total_boundary_crossings": total_crossings,
            "emergency_calls_made": successful_calls,
            "boundary_violations": violations,
            "emergency_system_ready": EMERGENCY_SYSTEM.twilio_client is not None
        }
        
        if latest_event:
            status.update({
                "latest_position": {
                    "latitude": latest_event[1],
                    "longitude": latest_event[2],
                    "inside_boundary": bool(latest_event[3]),
                    "distance_to_boundary": latest_event[5],
                    "timestamp": datetime.fromtimestamp(latest_event[0]).isoformat()
                }
            })
        
        return jsonify(status), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Create demo boundaries for testing
def create_demo_boundaries():
    """Create realistic maritime boundary data for testing"""
    zone_file = "india_maritime_zone.geojson"
    boundary_file = "india_pakistan_boundary.geojson"
    
    if not os.path.exists(zone_file):
        # Simplified Indian maritime zone
        india_zone = {
            "type": "Feature",
            "properties": {"name": "india_maritime_zone"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [68.0, 20.0], [75.0, 20.0], [75.0, 26.0], [68.0, 26.0], [68.0, 20.0]
                ]]
            }
        }
        
        with open(zone_file, "w") as f:
            json.dump(india_zone, f, indent=2)
        print(f"Created demo {zone_file}")
    
    if not os.path.exists(boundary_file):
        # India-Pakistan maritime boundary line
        boundary_line = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": "india_pakistan_maritime_boundary"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [68.0, 23.0], [68.0, 24.0], [68.0, 25.0], [68.0, 26.0]
                    ]
                }
            }]
        }
        
        with open(boundary_file, "w") as f:
            json.dump(boundary_line, f, indent=2)
        print(f"Created demo {boundary_file}")

def load_geojson_polygon(path: str):
    """Load polygon from GeoJSON file"""
    with open(path, "r", encoding="utf-8") as f:
        gj = json.load(f)
    
    if gj.get("type") == "Feature":
        geom = gj.get("geometry")
    elif gj.get("type") in ("Polygon", "MultiPolygon"):
        geom = gj
    else:
        raise ValueError(f"No polygon geometry found in {path}")
    
    return shape(geom)

def load_geojson_lines(path: str) -> List[LineString]:
    """Load line strings from GeoJSON file"""
    with open(path, "r", encoding="utf-8") as f:
        gj = json.load(f)
    
    lines = []
    if gj.get("type") == "FeatureCollection":
        for feat in gj.get("features", []):
            g = feat.get("geometry", {})
            if g.get("type") == "LineString":
                lines.append(LineString(g["coordinates"]))
    elif gj.get("type") == "Feature":
        g = gj.get("geometry", {})
        if g.get("type") == "LineString":
            lines.append(LineString(g["coordinates"]))
    
    return lines

def main():
    """Initialize and run the enhanced BlueNet boundary crossing system"""
    parser = argparse.ArgumentParser(description="BlueNet Enhanced Boundary Crossing System")
    parser.add_argument("--test-mode", action="store_true", 
                       help="Start with boundary crossing test")
    parser.add_argument("--port", type=int, default=5000, help="Server port")
    args = parser.parse_args()
    
    print("🌊 BlueNet Enhanced Boundary Crossing System")
    print("=" * 60)
    
    if not SHAPELY_AVAILABLE:
        print("Missing required packages!")
        print("Run: pip install shapely geopy twilio flask")
        return
    
    # Initialize
    Config.SERVER_PORT = args.port
    Config.TEST_MODE = args.test_mode
    
    print("📊 Initializing database...")
    init_database()
    
    print("🗺 Loading boundary data...")
    create_demo_boundaries()
    
    try:
        india_polygon = load_geojson_polygon("india_maritime_zone.geojson")
        boundary_lines = load_geojson_lines("india_pakistan_boundary.geojson")
        print(f"Loaded Indian maritime zone with {len(boundary_lines)} boundary segments")
    except Exception as e:
        print(f"Failed to load boundary data: {e}")
        return
    
    # Initialize systems
    global ENGINE, TEST_SYSTEM
    ENGINE = BoundaryCrossingEngine(india_polygon, boundary_lines)
    TEST_SYSTEM = BoundaryTestingSystem(ENGINE, EMERGENCY_SYSTEM)
    
    print("🛡 Boundary crossing detection ready")
    print(f"📞 Emergency calling: {'Ready' if EMERGENCY_SYSTEM.twilio_client else 'Simulation mode'}")
    print(f"📱 Emergency contact: {Config.RECIPIENT_PHONE_NUMBER}")
    
    # Start test mode if requested
    if Config.TEST_MODE:
        TEST_SYSTEM.start_testing()
        print("🧪 Boundary crossing test mode activated")
    
    print(f"\n🚀 Starting server on port {Config.SERVER_PORT}")
    print(f"📡 Dashboard: http://localhost:{Config.SERVER_PORT}")
    print(f"📍 GPS Endpoint: http://localhost:{Config.SERVER_PORT}/gps")
    print(f"📊 Status API: http://localhost:{Config.SERVER_PORT}/boundary-status")
    
    print("\n🧪 Test Commands:")
    print(f"# Test inside Indian waters:")
    print(f"curl 'http://localhost:{Config.SERVER_PORT}/gps?lat=22.5&lon=69.0'")
    print(f"# Test boundary crossing (triggers emergency call):")
    print(f"curl 'http://localhost:{Config.SERVER_PORT}/gps?lat=24.05&lon=67.95'")
    print(f"# Start automated testing:")
    print(f"curl 'http://localhost:{Config.SERVER_PORT}/start-boundary-test'")
    
    print(f"\n⚙ Configuration:")
    print(f"- Boundary Buffer: {Config.BOUNDARY_BUFFER_METERS}m")
    print(f"- Call Cooldown: {Config.CALL_COOLDOWN_SECONDS}s")
    print(f"- Max Calls per Crossing: {Config.MAX_EMERGENCY_CALLS}")
    
    print("\n" + "=" * 60)
    print("🚨 BOUNDARY CROSSING ALERT SYSTEM ACTIVE")
    print("=" * 60)
    
    try:
        app.run(
            host="0.0.0.0",
            port=Config.SERVER_PORT,
            debug=False,
            threaded=True,
            use_reloader=False
        )
    except KeyboardInterrupt:
        print("\n🛑 System stopped")
        if TEST_SYSTEM:
            TEST_SYSTEM.stop_testing()
    except Exception as e:
        print(f"\nSystem error: {e}")
        if TEST_SYSTEM:
            TEST_SYSTEM.stop_testing()
            
            
if __name__ == "__main__":
    main()