from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    # Development server
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)), debug=debug_mode)
