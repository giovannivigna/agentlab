FROM python:3.13-slim

# MCP Python SDK 2.x, where the server class is MCPServer, at
# mcp.server.mcpserver. 1.x called it FastMCP and put it elsewhere.
# Pillow is the image parser - the reason this server is in a box at all.
RUN pip install --no-cache-dir "mcp>=2.2,<3" "pillow>=11" \
 && useradd --create-home --uid 1000 mcp

WORKDIR /app
COPY server.py .
USER mcp
EXPOSE 8000
CMD ["python", "server.py"]
