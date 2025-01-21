# NOTE nixpacks triggers a python build when a main.py is present

from app.server import api_app  # noqa: F401

# https://github.com/fastapi/fastapi-cli/blob/9a4741816dc288bbd931e693166117d98ee14dea/src/fastapi_cli/cli.py#L162-L171
if __name__ == "__main__":
    import uvicorn
    from decouple import config

    PORT = config("PORT", cast=int)

    uvicorn.run(
        api_app,
        # bind on all interfaces in production
        host="0.0.0.0",
        port=PORT,
        # TODO we should tune this and have it pulled from env
        # workers=2,
        # NOTE important to ensure structlog controls logging in production
        log_config=None,
    )
