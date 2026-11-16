import asyncio


class HealthServer:
    """Lightweight /healthz HTTP server with zero extra dependencies (RNF10)."""

    def __init__(self, port: int = 8080) -> None:
        self._port = port
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await reader.readline()
            path = request_line.decode("ascii", errors="ignore").split(" ")[1:2]
            if path == ["/healthz"]:
                body = b'{"status":"ok"}'
                header = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                )
            else:
                body = b'{"error":"not_found"}'
                header = (
                    b"HTTP/1.1 404 Not Found\r\n"
                    b"Content-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                )
            writer.write(header + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
