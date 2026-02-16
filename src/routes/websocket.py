from flask import request
from flask_socketio import emit
import os
import select
from src.services.tmux import tmux_service

def register_handlers(socketio):
    
    @socketio.on('connect')
    def handle_connect():
        emit('connected', {'data': 'Connected'})
    
    @socketio.on('attach_session')
    def attach_session(data):
        session_name = data['session']
        terminal = data.get('terminal', 1)
        sid = f"{request.sid}_{terminal}"
        client_sid = request.sid  # Capture this before background task
        
        print(f"Attaching session {session_name} to terminal {terminal}, sid={sid}")
        
        # Clean up any existing session for this terminal
        tmux_service.cleanup_session(sid)
        
        master = tmux_service.attach_session(session_name, sid)
        
        print(f"Master fd: {master}")
        
        def read_output():
            print(f"Starting read_output loop for {sid}")
            while tmux_service.has_session(sid):
                r, _, _ = select.select([master], [], [], 0.1)
                if r:
                    try:
                        output = os.read(master, 10240)
                        if output:
                            decoded = output.decode('utf-8', errors='ignore')
                            print(f"Sending {len(decoded)} chars to terminal {terminal}")
                            socketio.emit('output', {'terminal': terminal, 'data': decoded}, room=client_sid)
                    except Exception as e:
                        print(f"Read error: {e}")
                        break
            print(f"Exiting read_output loop for {sid}")
        
        socketio.start_background_task(read_output)
    
    @socketio.on('create_session')
    def create_session(data):
        name = tmux_service.create_session(data['name'])
        emit('session_created', {'name': name})
    
    @socketio.on('input')
    def handle_input(data):
        terminal = data.get('terminal', 1)
        sid = f"{request.sid}_{terminal}"
        tmux_service.write_input(sid, data['data'])
    
    @socketio.on('resize')
    def handle_resize(data):
        terminal = data.get('terminal', 1)
        sid = f"{request.sid}_{terminal}"
        tmux_service.resize_terminal(sid, data['rows'], data['cols'])
    
    @socketio.on('close_session')
    def close_session(data):
        session_name = data['session']
        tmux_service.kill_session(session_name)
        emit('session_closed', {'session': session_name}, broadcast=True)
    
    @socketio.on('disconnect')
    def handle_disconnect():
        tmux_service.cleanup_session(f"{request.sid}_1")
        tmux_service.cleanup_session(f"{request.sid}_2")
