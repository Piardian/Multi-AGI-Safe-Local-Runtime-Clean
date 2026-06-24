# -*- coding: utf-8 -*-
"""ChatGPT browser bridge.

This provider talks to an already-open Chrome instance through the remote
debugging port. Start Chrome with --remote-debugging-port=9222 and log in to
ChatGPT once; Python can then paste prompts and read the latest answer.
"""

from __future__ import annotations

import time
import subprocess
import sys
from pathlib import Path
from urllib import request, error

import config


def _compose_prompt(messages: list[dict]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def ask_chatgpt(messages: list[dict], timeout: int | None = None) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "browser_gpt icin playwright kurulu olmali. requirements.txt bagimliliklarini kurun."
        ) from exc

    prompt = _compose_prompt(messages)
    timeout_ms = int((timeout or config.BROWSER_GPT_TIMEOUT) * 1000)
    endpoint = f"http://127.0.0.1:{config.CHROME_DEBUG_PORT}"
    started_process = None

    if config.BROWSER_GPT_AUTO_START and not _debug_port_ready():
        started_process = start_chatgpt_chrome()
        _wait_for_debug_port(timeout=30)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(endpoint)
        except Exception as exc:
            raise RuntimeError(
                "Chrome debug portuna baglanilamadi. Chrome'u "
                f"--remote-debugging-port={config.CHROME_DEBUG_PORT} ile acip ChatGPT'ye giris yapin."
            ) from exc

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        try:
            page = _find_or_open_chatgpt_page(context)
        except Exception as exc:
            raise RuntimeError(f"Browser provider hedefe ulasamadi: {config.BROWSER_GPT_URL}") from exc

        try:
            input_box = _find_input_box(page, timeout_ms)
        except Exception as exc:
            if sys.stdin.isatty():
                print("Lutfen tarayicida giris yapin, sonra Enter'a basin.")
                input()
                input_box = _find_input_box(page, timeout_ms)
            else:
                raise RuntimeError(
                    "Login veya prompt kutusu hazir degil. Lutfen tarayicida giris yapin, sonra tekrar deneyin."
                ) from exc
        _raise_if_blocking_modal(page)
        before = _latest_answer_text(page)
        input_box.click()
        input_box.fill(prompt)
        _submit_prompt(page)

        try:
            answer = _wait_for_new_answer(page, before, timeout_ms)
        except Exception as exc:
            raise RuntimeError(f"Browser provider cevap alamadi; timeout={timeout_ms // 1000}s. Sebep: {exc}") from exc
        if config.BROWSER_GPT_CLOSE_AFTER:
            try:
                browser.close()
            finally:
                if started_process:
                    started_process.terminate()
        return answer


def start_chatgpt_chrome() -> subprocess.Popen:
    chrome_path = config.CHROME_PATH or _find_chrome_path()
    profile_dir = Path(config.BROWSER_GPT_PROFILE_DIR)
    if not profile_dir.is_absolute():
        profile_dir = Path(config.PROJECT_ROOT) / profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)

    args = [
        chrome_path,
        f"--remote-debugging-port={config.CHROME_DEBUG_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        config.BROWSER_GPT_URL,
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _find_chrome_path() -> str:
    candidates = [
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("Chrome/Edge bulunamadi. .env icinde CHROME_PATH ayarlayin.")


def _debug_port_ready() -> bool:
    try:
        with request.urlopen(f"http://127.0.0.1:{config.CHROME_DEBUG_PORT}/json/version", timeout=1):
            return True
    except (error.URLError, TimeoutError, OSError):
        return False


def _wait_for_debug_port(timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _debug_port_ready():
            return
        time.sleep(0.5)
    raise RuntimeError("Chrome debug portu zamaninda acilmadi.")


def _find_or_open_chatgpt_page(context):
    target_host = "chatgpt.com"
    if "gemini.google.com" in config.BROWSER_GPT_URL:
        target_host = "gemini.google.com"
    elif "claude.ai" in config.BROWSER_GPT_URL:
        target_host = "claude.ai"
    elif "groq.com" in config.BROWSER_GPT_URL:
        target_host = "groq.com"
    elif "perplexity.ai" in config.BROWSER_GPT_URL:
        target_host = "perplexity.ai"

    for page in context.pages:
        if target_host in page.url:
            page.bring_to_front()
            return page
    page = context.new_page()
    page.goto(config.BROWSER_GPT_URL, wait_until="domcontentloaded")
    return page


def _find_input_box(page, timeout_ms: int):
    selectors = [
        "#prompt-textarea",
        "textarea[data-testid='prompt-textarea']",
        "div[contenteditable='true']",
        "textarea",
    ]
    last_error = None
    for selector in selectors:
        try:
            locator = page.locator(selector).last
            locator.wait_for(state="visible", timeout=timeout_ms // 3)
            return locator
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"ChatGPT prompt kutusu bulunamadi: {last_error}")


def _submit_prompt(page) -> None:
    selectors = [
        "button[data-testid='send-button']",
        "button[aria-label*='Send']",
        "button[aria-label*='Gönder']",
    ]
    for selector in selectors:
        button = page.locator(selector).last
        try:
            if button.count() and button.is_enabled():
                button.click()
                return
        except Exception:
            pass
    page.keyboard.press("Enter")


def _raise_if_blocking_modal(page) -> None:
    selectors = [
        "#modal-no-auth-rate-limit",
        "[data-testid='modal-no-auth-rate-limit']",
        "[role='dialog']",
    ]
    for selector in selectors:
        try:
            item = page.locator(selector).first
            if item.count() and item.is_visible(timeout=500):
                text = item.inner_text(timeout=1000).strip()
                if "log" in text.lower() or "limit" in text.lower() or selector != "[role='dialog']":
                    raise RuntimeError("Lutfen tarayicida giris yapin, sonra Enter'a basin. ChatGPT login/rate-limit modali gorunuyor.")
        except RuntimeError:
            raise
        except Exception:
            pass


def _latest_answer_text(page) -> str:
    selectors = [
        "[data-message-author-role='assistant']",
        "article",
        ".markdown",
    ]
    for selector in selectors:
        try:
            items = page.locator(selector)
            count = items.count()
            if count:
                return items.nth(count - 1).inner_text(timeout=2000).strip()
        except Exception:
            pass
    return ""


def _wait_for_new_answer(page, before: str, timeout_ms: int) -> str:
    deadline = time.time() + timeout_ms / 1000
    stable_answer = ""
    stable_count = 0

    while time.time() < deadline:
        answer = _latest_answer_text(page)
        if answer and answer != before:
            if answer == stable_answer:
                stable_count += 1
            else:
                stable_answer = answer
                stable_count = 0
            if stable_count >= 3:
                return answer.strip()
        time.sleep(1)

    if stable_answer:
        return stable_answer.strip()
    raise RuntimeError("ChatGPT cevabi zaman asimina ugradi.")
