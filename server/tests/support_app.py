"""仅由 pytest 启动的额外探针；不属于产品 API 或部署入口。"""

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from itime.main import create_app


class InputProbe(BaseModel):
    count: int = Field(ge=1, le=10)
    password: str


def create_test_app() -> FastAPI:
    app = create_app()
    assert app.state.settings.environment == "test"

    @app.post("/__test/input")
    async def input_probe(body: InputProbe) -> dict:
        return {"count": body.count}

    @app.post("/__test/body")
    async def body_probe(request: Request) -> dict:
        return {"bytes": len(await request.body())}

    @app.get("/__test/error")
    async def unexpected_error() -> None:
        raise ValueError("sensitive-exception-content-do-not-log")

    @app.get("/__test/database")
    async def database_probe() -> dict:
        async with app.state.database.transaction() as session:
            value = await session.scalar(text("SELECT 1"))
        return {"value": value}

    return app
