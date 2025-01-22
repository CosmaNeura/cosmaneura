"""
- https://pypi.org/project/fastapi-structlog/0.5.0/
- https://pypi.org/project/asgi-correlation-id/#description
- https://gist.github.com/nymous/f138c7f06062b7c43c060bf03759c29e
- https://github.com/sharu1204/fastapi-structlog/blob/master/app/main.py
"""


@app.middleware("http")
async def logging_middleware(request: Request, call_next) -> Response:
    # clear the threadlocal context
    structlog.threadlocal.clear_threadlocal()
    # bind threadlocal
    structlog.threadlocal.bind_threadlocal(
        logger="uvicorn.access",
        request_id=str(uuid.uuid4()),
        cookies=request.cookies,
        scope=request.scope,
        url=str(request.url),
    )
    try:
        start_time = time.time()
        response: Response = await call_next(request)
    finally:
        process_time = time.time() - start_time
        log.info(
            "processed a request",
            status_code=response.status_code,
            process_time=process_time,
        )
    return response
