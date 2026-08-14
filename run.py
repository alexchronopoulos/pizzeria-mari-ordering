from app import create_app
from app.startup_performance import prepare_app_for_serving


app = create_app()
prepare_app_for_serving(app, version="0.18.26")


if __name__ == "__main__":
    app.run()
