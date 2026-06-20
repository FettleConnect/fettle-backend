from __future__ import annotations



class TestHealthEndpoint:
    def test_health_returns_200(self, sync_client) -> None:
        response = sync_client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_json(self, sync_client) -> None:
        response = sync_client.get("/health")
        assert response.json() == {"status": "ok"}

    def test_health_content_type_is_json(self, sync_client) -> None:
        response = sync_client.get("/health")
        assert "application/json" in response.headers["content-type"]

    async def test_health_async(self, async_client) -> None:
        response = await async_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
