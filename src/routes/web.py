from flask import Blueprint, render_template
from src.services.tmux import tmux_service

bp = Blueprint('web', __name__)

@bp.route('/')
def index():
    sessions = tmux_service.list_sessions()
    return render_template('index.html', sessions=sessions)

@bp.route('/history/<session_name>')
def session_history(session_name):
    history = tmux_service.get_history(session_name)
    return render_template('history.html', session=session_name, history=history)
