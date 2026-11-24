#!/usr/bin/env python3
"""本地字体工坊 · 本地网页服务（仅监听 127.0.0.1）。"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socket
import sys
import threading
import time
import traceback
import uuid
import webbrowser
import zipfile
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from handfont.chars import PRESETS, clean_chars, load_preset
from handfont.config import (
    DEFAULT_COLS,
    DEFAULT_ROWS,
    MAX_COLS,
    MAX_ROWS,
    MIN_COLS,
    MIN_ROWS,
    PROJECTS_DIR,
    WEB_DIR,
    ensure_dirs,
)
from handfont.pipeline import build_project
from handfont.template import generate_template
from handfont.util import natural_key, read_json, safe_filename, slugify, write_json

MAX_BODY = 300 * 1024 * 1024  # 300 MB
MAX_FILE = 45 * 1024 * 1024
VERSION = "1.0.0"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------- jobs


def _update_job(job_id: str, **values) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(values)


def start_build_job(project: Path, photos: list[Path], options: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "running",
            "progress": 0.0,
            "done": 0,
            "total": 0,
            "message": "准备中…",
            "result": None,
            "error": None,
            "started": time.time(),
        }

    def progress(done: int, total: int, message: str) -> None:
        _update_job(
            job_id,
            done=done,
            total=total,
            progress=(done / total) if total else 0.0,
            message=message,
        )

    def run() -> None:
        try:
            result = build_project(
                project,
                photos,
                family=options.get("family"),
                style=options.get("style", "Regular"),
                normalize=options.get("normalize", True),
                progress=progress,
            )
            _update_job(job_id, status="done", progress=1.0, message="完成", result=result)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            _update_job(job_id, status="error", error=str(exc), message="失败")

    threading.Thread(target=run, daemon=True).start()
    return job_id


# ---------------------------------------------------------------- helpers


def project_url(project_id: str, rel: str = "") -> str:
    base = f"/projects/{project_id}"
    return f"{base}/{rel.lstrip('/')}" if rel else base


def ensure_png_zip(project: Path, meta: dict) -> Path | None:
    """旧项目没有 PNG 压缩包时按需补生成。"""
    zip_path = project / "template_png.zip"
    if zip_path.exists():
        return zip_path
    files = [
        project / page["file"]
        for page in meta.get("pages", [])
        if (project / page["file"]).exists()
    ]
    if not files:
        return None
    tmp = project / f".template_png.{time.time_ns()}.tmp"
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
            for file in files:
                zf.write(file, arcname=file.name)
        tmp.replace(zip_path)
        return zip_path
    except Exception:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return None


def project_summary(path: Path) -> dict | None:
    meta_path = path / "meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = read_json(meta_path)
    except Exception:  # noqa: BLE001
        return None
    result_path = path / "build" / "result.json"
    result = None
    if result_path.exists():
        try:
            result = read_json(result_path)
        except Exception:  # noqa: BLE001
            result = None
    sample = "".join("".join(p.get("chars") or []) for p in meta.get("pages", []))[:60]
    png_zip = ensure_png_zip(path, meta)
    pngs = [project_url(path.name, p["file"]) for p in meta.get("pages", []) if p.get("file")]
    return {
        "id": path.name,
        "title": meta.get("title") or meta.get("family_name") or path.name,
        "family": meta.get("family_name") or meta.get("title"),
        "sample": sample,
        "created_at": meta.get("created_at"),
        "total_chars": meta.get("total_chars", 0),
        "pages": len(meta.get("pages", [])),
        "cols": meta.get("layout", {}).get("cols"),
        "rows": meta.get("layout", {}).get("rows"),
        "pdf": project_url(path.name, meta["pdf"]) if meta.get("pdf") else None,
        "png_zip": project_url(path.name, png_zip.name) if png_zip else None,
        "pngs": pngs,
        "preview": project_url(path.name, meta["pages"][0]["preview"]) if meta.get("pages") else None,
        "font": project_url(path.name, result["font"]) if result and result.get("font") else None,
        "font_preview": project_url(path.name, result["preview"]) if result and result.get("preview") else None,
        "glyphs": result.get("glyphs") if result else None,
    }


def list_projects() -> list[dict]:
    ensure_dirs()
    items = []
    for path in PROJECTS_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        summary = project_summary(path)
        if summary:
            items.append(summary)
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def resolve_under(base: Path, rel: str) -> Path | None:
    try:
        candidate = (base / unquote(rel)).resolve()
        base = base.resolve()
        if candidate == base or base in candidate.parents:
            return candidate
    except Exception:  # noqa: BLE001
        return None
    return None


def parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    ctype = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in ctype:
        raise ValueError("需要 multipart/form-data")
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > MAX_BODY:
        raise ValueError("请求体过大或为空")
    body = handler.rfile.read(length)
    msg = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + ctype.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename:
            files.append((filename, data))
        elif name:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


# ---------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = f"HandFontStudio/{VERSION}"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if os.environ.get("HANDFONT_VERBOSE"):
            super().log_message(fmt, *args)

    # -- responses -------------------------------------------------
    def send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def send_file(self, path: Path, download_name: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error_json("文件不存在", 404)
            return
        ctype, _ = mimetypes.guess_type(path.name)
        if path.suffix.lower() == ".ttf":
            ctype = "font/ttf"
        elif path.suffix.lower() == ".pdf":
            ctype = "application/pdf"
        ctype = ctype or "application/octet-stream"
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-cache")
        if download_name:
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{_quote(download_name)}",
            )
        self.end_headers()
        with open(path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    # -- GET -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in {"/", "/index.html"}:
            self.send_file(WEB_DIR / "index.html")
            return
        if path.startswith("/static/"):
            target = resolve_under(WEB_DIR, path[len("/static/") :])
            if target:
                self.send_file(target)
            else:
                self.send_error_json("非法路径", 403)
            return
        if path.startswith("/projects/"):
            rel = path[len("/projects/") :]
            if ".." in rel:
                self.send_error_json("非法路径", 403)
                return
            target = resolve_under(PROJECTS_DIR, rel)
            if target:
                download = parse_qs(parsed.query).get("download")
                self.send_file(target, download_name=target.name if download else None)
            else:
                self.send_error_json("非法路径", 403)
            return
        if path == "/api/config":
            self.send_json(
                {
                    "ok": True,
                    "version": VERSION,
                    "presets": [
                        {
                            "id": key,
                            "label": val["label"],
                            "desc": val["desc"],
                            "count": len(clean_chars(load_preset(key))[0]) if key != "custom" else 0,
                        }
                        for key, val in PRESETS.items()
                    ],
                    "defaults": {
                        "cols": DEFAULT_COLS,
                        "rows": DEFAULT_ROWS,
                        "family": "我的手写体",
                        "sample": "永和九年，岁在癸丑，暮春之初。会于会稽山阴之兰亭，群贤毕至，少长咸集。",
                    },
                    "limits": {"cols": [MIN_COLS, MAX_COLS], "rows": [MIN_ROWS, MAX_ROWS]},
                    "potrace": bool(shutil.which("potrace")),
                }
            )
            return
        if path == "/api/projects":
            self.send_json({"ok": True, "projects": list_projects()})
            return
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/") :]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id) or {})
            if not job:
                self.send_error_json("任务不存在", 404)
                return
            if job.get("result"):
                job["result"] = self._absolutize_result(job["result"])
            self.send_json({"ok": True, "job": job})
            return
        self.send_error_json("未找到", 404)

    def _absolutize_result(self, result: dict) -> dict:
        out = dict(result)
        # 结果中的路径是项目内相对路径，补成可下载 URL 需要项目 id；由前端拼接。
        return out

    # -- POST ------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/template":
                self.handle_template()
            elif parsed.path == "/api/build":
                self.handle_build(parsed.query)
            else:
                self.send_error_json("未找到", 404)
        except ValueError as exc:
            self.send_error_json(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.send_error_json(f"服务器错误：{exc}", 500)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2 * 1024 * 1024:
            raise ValueError("请求体为空或过大")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def handle_template(self) -> None:
        data = self.read_json()
        preset = str(data.get("preset") or "custom")
        text = str(data.get("text") or "")
        if preset != "custom":
            text = load_preset(preset)
        chars, _ = clean_chars(text)
        cols = int(data.get("cols") or DEFAULT_COLS)
        rows = int(data.get("rows") or DEFAULT_ROWS)
        if not (MIN_COLS <= cols <= MAX_COLS and MIN_ROWS <= rows <= MAX_ROWS):
            raise ValueError(f"行列数需在 {MIN_COLS}–{MAX_COLS} 列、{MIN_ROWS}–{MAX_ROWS} 行之间")
        family = str(data.get("family") or "我的手写体").strip()[:40] or "我的手写体"
        title = family
        label = PRESETS.get(preset, {}).get("label", "自定义文字")
        if preset == "custom":
            label = f"自定义 {len(chars)} 字"

        ensure_dirs()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        project_id = f"{stamp}-{slugify(family)}-{uuid.uuid4().hex[:4]}"
        project = PROJECTS_DIR / project_id
        project.mkdir(parents=True, exist_ok=False)

        meta = generate_template(
            project,
            chars,
            title=title,
            family_name=family,
            preset=preset,
            preset_label=label,
            cols=cols,
            rows=rows,
        )
        summary = project_summary(project)
        self.send_json(
            {
                "ok": True,
                "project": summary,
                "meta": {
                    "total_chars": len(chars),
                    "pages": len(meta["pages"]),
                    "cols": cols,
                    "rows": rows,
                    "page_width": meta.get("png_page", meta["page"])["width"],
                    "page_height": meta.get("png_page", meta["page"])["height"],
                    "dpi": meta.get("png_page", meta["page"])["dpi"],
                    "pdf": project_url(project_id, "template.pdf") if meta.get("pdf") else None,
                    "png_zip": project_url(project_id, "template_png.zip") if meta.get("png_zip") else None,
                    "pngs": [
                        project_url(project_id, p["file"]) for p in meta["pages"]
                    ],
                    "thumbs": [
                        project_url(project_id, p["preview"]) for p in meta["pages"][:3]
                    ],
                    "chars": "".join(chars[:60]),
                },
            }
        )

    def handle_build(self, query: str) -> None:
        qs = parse_qs(query)
        project_id = (qs.get("project") or [""])[0]
        if not project_id or "/" in project_id or ".." in project_id:
            raise ValueError("缺少项目")
        project = PROJECTS_DIR / project_id
        if not (project / "meta.json").exists():
            raise ValueError("项目不存在")

        fields, files = parse_multipart(self)
        if not files:
            raise ValueError("没有收到照片")
        if len(files) > 120:
            raise ValueError("一次最多上传 120 张照片")

        upload_dir = project / "uploads" / time.strftime("%Y%m%d-%H%M%S")
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved: list[Path] = []
        for filename, data in files:
            if len(data) > MAX_FILE:
                raise ValueError(f"图片过大（>45MB）：{filename}")
            ext = Path(filename).suffix.lower() or ".jpg"
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
                ext = ".jpg"
            name = safe_filename(filename, "photo") or "photo"
            if not name.lower().endswith(ext):
                name = Path(name).stem + ext
            target = upload_dir / name
            i = 1
            while target.exists():
                target = upload_dir / f"{Path(name).stem}_{i}{ext}"
                i += 1
            target.write_bytes(data)
            saved.append(target)

        family = (fields.get("family") or "").strip()[:40] or None
        normalize = (fields.get("normalize") or "1") not in {"0", "false", "off"}
        job_id = start_build_job(
            project,
            sorted(saved, key=natural_key),
            {"family": family, "normalize": normalize},
        )
        self.send_json({"ok": True, "job": job_id, "project": project_id, "files": len(saved)})


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


# ---------------------------------------------------------------- main


def find_port(start: int) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def main(port: int = 8765, open_browser: bool = True) -> int:
    ensure_dirs()
    if not (WEB_DIR / "index.html").exists():
        print(f"缺少网页文件：{WEB_DIR / 'index.html'}", file=sys.stderr)
        return 1
    port = find_port(port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("本地字体工坊已启动")
    print(f"  {url}")
    print("  只监听本机，不会把照片传到互联网。按 Ctrl+C 停止。")
    if open_browser:
        threading.Timer(0.9, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
