from __future__ import annotations
from flask import Flask
from flask_socketio import SocketIO
from pathlib import Path
from .helpers import ensure_dirs

def create_app(cfg: dict):
    # Setup static folder
    static_folder = Path(__file__).parent / 'static'
    app = Flask(__name__, static_folder=str(static_folder), static_url_path='/static')
    app.config["PICAM_CFG"] = cfg or {}

    # Khởi tạo SocketIO cho WebSocket proxy
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

    # Đảm bảo thư mục ghi hình tồn tại
    record_root = Path(((cfg.get("paths") or {}).get("record_root") or "/media/ssd/picam"))
    ensure_dirs(record_root)

    # Đăng ký các blueprint
    from .bp_dashboard import bp as bp_dash
    from .bp_liveview  import bp as bp_live
    from .bp_storage   import bp as bp_store
    from .bp_actions   import bp as bp_act
    app.register_blueprint(bp_dash)
    app.register_blueprint(bp_live)
    app.register_blueprint(bp_store)
    app.register_blueprint(bp_act)

    # Setup WebSocket proxy handlers
    from .bp_liveview import setup_socketio_proxy
    setup_socketio_proxy(socketio)

    return app, socketio
