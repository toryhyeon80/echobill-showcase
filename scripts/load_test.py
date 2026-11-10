"""
동시 접속 시험 (PM 10/21) — /parse-audio N건을 한꺼번에 보내면서 가벼운 조회(/ledger, /dashboard/unpaid)가
얼마나 느려지는지, 어디서 먼저 막히는지 잰다. 서버는 scripts/load_test_server.py (STT·LLM 가짜 지연).

    backend/.venv/bin/python scripts/load_test.py --base http://127.0.0.1:8010 --audio 20 30 [--out result.json]

음성 요청은 사용자마다 1건(서로 다른 dev-token 사용자), 조회는 다른 사용자 1명이 --light-interval초마다
/ledger와 /dashboard/unpaid를 번갈아 보낸다. 시작 전에 조회만 10번 보내 평소 응답 시간을 잰다.
결과: 종류별 p50·p95·최대(ms), 상태 코드·오류 코드 수. 로컬·테스트 서버 전용 — 운영 주소면 시작하지 않는다.
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections import Counter

import httpx

AUDIO = b"\x1aE\xdf\xa3" + b"\0" * 2000  # webm 머리 + 빈 내용 — 서버의 STT는 가짜라 내용은 읽지 않음
LIGHT_PATHS = ("/api/v1/ledger", "/api/v1/dashboard/unpaid")


def _pct(values: list[float], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(q * len(ordered)) - 1))])


async def _token(client: httpx.AsyncClient) -> str:
    res = await client.post("/api/v1/auth/dev-token", json={"user_id": f"load-{uuid.uuid4().hex[:12]}"})
    res.raise_for_status()
    return res.json()["access_token"]


async def _timed(client: httpx.AsyncClient, kind: str, method: str, path: str, **kwargs) -> dict:
    started = time.perf_counter()
    try:
        res = await client.request(method, path, **kwargs)
        code = None
        if res.status_code >= 400:
            try:
                code = res.json().get("error_code") or res.json().get("detail")
            except ValueError:
                code = res.text[:60]
        return {"kind": kind, "status": res.status_code, "code": code,
                "ms": (time.perf_counter() - started) * 1000, "at": started}
    except httpx.HTTPError as exc:
        return {"kind": kind, "status": None, "code": type(exc).__name__,
                "ms": (time.perf_counter() - started) * 1000, "at": started}


async def scenario(base: str, audio_count: int, light_interval: float, timeout: float) -> dict:
    limits = httpx.Limits(max_connections=audio_count + 10, max_keepalive_connections=audio_count + 10)
    async with httpx.AsyncClient(base_url=base, timeout=timeout, limits=limits) as client:
        light_token = await _token(client)
        light_headers = {"Authorization": f"Bearer {light_token}"}
        baseline = [await _timed(client, "조회(평소)", "GET", LIGHT_PATHS[i % 2], headers=light_headers)
                    for i in range(10)]
        tokens = await asyncio.gather(*(_token(client) for _ in range(audio_count)))

        async def voice(token: str) -> dict:
            headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}
            return await _timed(client, "음성", "POST", "/api/v1/ledger/parse-audio", headers=headers,
                                files={"file": ("a.webm", AUDIO, "audio/webm")})

        done = asyncio.Event()

        async def light_loop() -> list[dict]:
            out, i = [], 0
            while not done.is_set():
                out.append(asyncio.create_task(
                    _timed(client, "조회(음성 중)", "GET", LIGHT_PATHS[i % 2], headers=light_headers)))
                i += 1
                await asyncio.sleep(light_interval)
            return list(await asyncio.gather(*out))

        started = time.perf_counter()
        light_task = asyncio.create_task(light_loop())
        voices = await asyncio.gather(*(voice(t) for t in tokens))
        done.set()
        lights = await light_task
        wall = (time.perf_counter() - started) * 1000

    rows = baseline + voices + lights
    summary = {"audio": audio_count, "wall_ms": round(wall), "kinds": {}}
    for kind in ("조회(평소)", "음성", "조회(음성 중)"):
        part = [r for r in rows if r["kind"] == kind]
        ok = [r["ms"] for r in part if r["status"] is not None and r["status"] < 400]
        summary["kinds"][kind] = {
            "n": len(part), "ok": len(ok),
            "p50": _pct([r["ms"] for r in part], .5), "p95": _pct([r["ms"] for r in part], .95),
            "max": _pct([r["ms"] for r in part], 1.0),
            "status": dict(Counter(str(r["status"]) for r in part)),
            "errors": dict(Counter(f"{r['status']}:{r['code']}" for r in part if r["code"])),
        }
    # 음성 응답 시간의 계단: 동시에 처리되는 건수가 막혀 있으면 STT 시간의 배수로 나뉜다
    voice_ms = sorted(r["ms"] for r in voices)
    summary["voice_waves"] = dict(Counter(f"{int(ms // 5000) * 5}-{int(ms // 5000) * 5 + 5}초" for ms in voice_ms))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--audio", type=int, nargs="+", default=[20, 30])
    parser.add_argument("--light-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", help="결과 JSON 파일")
    args = parser.parse_args()
    if not any(host in args.base for host in ("127.0.0.1", "localhost")):
        print("[중단] 로컬 서버(127.0.0.1·localhost)로만 시험합니다 — 운영·공유 서버 금지")
        return 1
    results = []
    for count in args.audio:
        print(f"[시험] 음성 {count}건 동시 + 조회 {args.light_interval:g}초마다")
        result = asyncio.run(scenario(args.base, count, args.light_interval, args.timeout))
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as stream:
            json.dump(results, stream, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
