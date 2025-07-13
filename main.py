from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os
import asyncio
from typing import Optional, Tuple, List


app = FastAPI()


class MoveRequest(BaseModel):
    version: str
    position: str
    depth: Optional[int] = 15


ENGINE_BINARIES = {
    "stockfish": "engines/stockfish",
    "0.3.0": "engines/engine-0.3.0"
}


def parse_uci_move(move: str) -> Tuple[str, str, Optional[str]]:
    """
    Parses a UCI move string into from, to, and promotion parts.
    Example: e2e4 -> from=e2, to=e4, promotion=None
             e7e8q -> from=e7, to=e8, promotion=q
    """
    if len(move) < 4:
        raise ValueError(f"Invalid move string: {move}")
    from_sq = move[0:2]
    to_sq = move[2:4]
    promotion = move[4] if len(move) == 5 else None
    return from_sq, to_sq, promotion


async def get_best_move(engine_path: str, fen: str, depth: int) -> Tuple[str, List[str]]:
    log_lines = []

    try:
        process = await asyncio.create_subprocess_exec(
            engine_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        async def send(cmd: str):
            log_lines.append(f">>> {cmd}")
            process.stdin.write(f"{cmd}\n".encode())
            await process.stdin.drain()

        async def readline():
            line = (await process.stdout.readline()).decode().strip()
            if line:
                log_lines.append(f"<<< {line}")
            return line

        async def read_stderr():
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                log_lines.append(f"!!! {line.decode().strip()}")

        stderr_task = asyncio.create_task(read_stderr())

        await send("uci")
        while True:
            line = await readline()
            if line == "uciok":
                break

        await send("isready")
        while True:
            line = await readline()
            if line == "readyok":
                break

        await send(f"position fen {fen}")
        await send(f"go depth {depth}")

        best_move = None
        while True:
            line = await readline()
            if line.startswith("bestmove"):
                best_move = line.split(" ")[1]
                break

        await send("quit")
        await process.wait()
        await stderr_task

        if best_move:
            return best_move, log_lines

        raise RuntimeError("Engine did not return a move.")

    except Exception as e:
        log_lines.append(f"!!! Exception: {str(e)}")
        raise RuntimeError("\n".join(log_lines))


@app.post("/bestmove")
async def bestmove(request: MoveRequest):
    engine_path = ENGINE_BINARIES.get(request.version)
    if not engine_path or not os.path.isfile(engine_path):
        raise HTTPException(status_code=404, detail="Engine version not found")

    try:
        best_move, logs = await get_best_move(engine_path, request.position, request.depth)

        from_sq, to_sq, promotion = parse_uci_move(best_move)

        return {
            "best_move": best_move,
            "from": from_sq,
            "to": to_sq,
            "promotion": promotion,
            "log": logs
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
