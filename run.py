from app import APP_VERSION, create_app
from app.startup_performance import prepare_app_for_serving


app = create_app()
prepare_app_for_serving(app, version=APP_VERSION)


if __name__ == "__main__":
    app.run()
