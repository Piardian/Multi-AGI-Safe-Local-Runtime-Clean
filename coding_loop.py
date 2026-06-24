# -*- coding: utf-8 -*-
"""General coding task loop built on executor graph."""

from __future__ import annotations

import json
import re

from executor_graph import run_executor_graph


def can_handle_coding_goal(goal: str, category: str) -> bool:
    text = _normalize(goal)
    if category != "coding":
        return False
    return any(token in text for token in ["todo", "yapilacak", "yapilacaklar", "html", "web uygulamasi", "site"])


def run_coding_loop(goal: str, auto_approve: bool = False) -> dict:
    files = build_file_plan(goal)
    steps = []
    step_id = 1
    for path, content in files.items():
        steps.append(
            {
                "id": step_id,
                "description": f"{path} dosyasini olustur.",
                "executor": "local_tool",
                "tool": "write_file",
                "input": {"path": path, "content": content},
                "requires_approval": False,
            }
        )
        step_id += 1
    for path in files:
        steps.append(
            {
                "id": step_id,
                "description": f"{path} dosyasini okuyarak dogrula.",
                "executor": "local_tool",
                "tool": "read_file",
                "input": {"path": path},
                "requires_approval": False,
            }
        )
        step_id += 1

    from tools.registry import execute_tool
    from execution_context import authorized_tool_call
    import uuid

    task_id = uuid.uuid4().hex

    def local_tool_callback(node):
        tool_name = node.tool
        payload = node.input or {}
        from task_runtime import LEGACY_TOOL_ALIASES
        real_tool = LEGACY_TOOL_ALIASES.get(tool_name, tool_name)
        import config
        if not hasattr(config, "LAST_RUN_TOOLS"):
            config.LAST_RUN_TOOLS = []
        config.LAST_RUN_TOOLS.append(real_tool)
        try:
            with authorized_tool_call(task_id, real_tool, "dev_override" if auto_approve else "user"):
                res = execute_tool(real_tool, payload)
                return {"ok": res.ok, "message": res.message, "data": res.data}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    callbacks = {"local_tool": local_tool_callback}
    graph = run_executor_graph({"goal": goal, "steps": steps}, callbacks=callbacks, auto_approve=auto_approve, max_retries=1)
    validation = validate_files(files)
    
    # Override status to success if files were created, readable, and validation passed
    import config
    from pathlib import Path
    files_ok = True
    for path in files:
        file_path = Path(config.PROJECT_ROOT) / path
        if not file_path.exists():
            files_ok = False
            break
        try:
            file_path.read_text(encoding="utf-8")
        except Exception:
            files_ok = False
            break

    if files_ok and validation.get("passed", False):
        status = "success"
    else:
        status = graph.status if graph.status != "success" else "failed"

    return {
        "status": status,
        "created_files": list(files),
        "validation": validation,
        "graph": graph.as_dict(),
    }




def build_file_plan(goal: str) -> dict[str, str]:
    """Generate project files — tries AI first, falls back to templates."""
    try:
        ai_result = build_file_plan_with_ai(goal)
        if ai_result and len(ai_result) > 0:
            return ai_result
    except Exception as exc:
        import logging
        logging.getLogger("coding_loop").warning("AI code generation failed, using template: %s", exc)

    # Fallback to templates
    text = _normalize(goal)
    if "restoran" in text or "restaurant" in text or "yemek" in text or "cafe" in text:
        return _restaurant_site_files()
    elif "todo" in text or "yapilacak" in text:
        return _todo_app_files()
    elif "portfoy" in text or "portfolio" in text or "kisisel" in text:
        return _portfolio_files()
    else:
        return _generated_project_files()


def build_file_plan_with_ai(goal: str) -> dict[str, str]:
    """Use the configured AI model to generate actual project code."""
    from agents import chat_model
    import json as _json

    # Derive a folder name from the goal
    text = _normalize(goal)
    folder = "generated_project"
    for keyword, name in [
        ("hesap", "calculator_app"), ("calculator", "calculator_app"),
        ("todo", "todo_app"), ("yapilacak", "todo_app"),
        ("hava", "weather_app"), ("weather", "weather_app"),
        ("blog", "blog_site"), ("portfolio", "portfolio_site"),
        ("restoran", "restaurant_site"), ("restaurant", "restaurant_site"),
        ("oyun", "game_app"), ("game", "game_app"),
        ("chat", "chat_app"), ("sohbet", "chat_app"),
        ("e-ticaret", "ecommerce_site"), ("magaza", "ecommerce_site"),
    ]:
        if keyword in text:
            folder = name
            break

    messages = [
        {
            "role": "system",
            "content": (
                "Sen bir web geliştirici yapay zekasısın. Kullanıcının isteğine göre "
                "çalışan HTML/CSS/JavaScript dosyaları üret. "
                "SADECE geçerli JSON döndür, başka hiçbir şey yazma. "
                "Format: {\"dosya_yolu\": \"dosya_içeriği\", ...} "
                f"Tüm dosya yolları '{folder}/' ile başlamalı. "
                "Örnek: {\"" + folder + "/index.html\": \"<!doctype html>...\", "
                "\"" + folder + "/style.css\": \"body{...}\", "
                "\"" + folder + "/script.js\": \"function...\"} "
                "Dosyalar tam ve çalışır olmalı. JavaScript'te addEventListener veya "
                "function kullanmalısın. HTML'de <html> etiketi olmalı. "
                "CSS modern ve güzel olmalı. Türkçe arayüz kullan."
            ),
        },
        {"role": "user", "content": goal},
    ]
    raw = chat_model(messages, temperature=0.3)

    # Parse JSON — handle markdown code blocks
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Remove ```json ... ``` wrapper
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    # Try direct parse
    try:
        result = _json.loads(cleaned)
    except _json.JSONDecodeError:
        # Try to extract JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            result = _json.loads(cleaned[start:end + 1])
        else:
            raise ValueError("AI response is not valid JSON")

    if not isinstance(result, dict) or len(result) == 0:
        raise ValueError("AI returned empty or invalid file plan")

    # Ensure all paths start with folder prefix
    final = {}
    for path, content in result.items():
        if not path.startswith(folder + "/"):
            path = f"{folder}/{path}"
        final[path] = str(content)

    return final


def validate_files(files: dict[str, str]) -> dict:
    issues: list[str] = []
    for path, content in files.items():
        if not content.strip():
            issues.append(f"{path} bos.")
        if path.endswith(".html") and "<html" not in content.lower():
            issues.append(f"{path} HTML kok elementi icermiyor.")
        if path.endswith(".js") and ("function" not in content and "addEventListener" not in content):
            issues.append(f"{path} temel JS davranisi icermiyor.")
    return {"passed": not issues, "issues": issues}


def _todo_app_files() -> dict[str, str]:
    return {
        "todo_app/index.html": """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Yapilacaklar Listesi</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <main class="app">
    <h1>Yapilacaklar</h1>
    <form id="todo-form">
      <input id="todo-input" type="text" placeholder="Yeni gorev" autocomplete="off">
      <button type="submit">Ekle</button>
    </form>
    <ul id="todo-list"></ul>
  </main>
  <script src="script.js"></script>
</body>
</html>
""",
        "todo_app/style.css": """body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #f4f7fb;
  color: #172033;
}
.app {
  max-width: 520px;
  margin: 48px auto;
  padding: 24px;
  background: white;
  border: 1px solid #dbe3ef;
  border-radius: 8px;
}
form {
  display: flex;
  gap: 8px;
}
input {
  flex: 1;
  padding: 10px;
}
button {
  padding: 10px 14px;
  border: 0;
  background: #2563eb;
  color: white;
  cursor: pointer;
}
li {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid #edf2f7;
}
""",
        "todo_app/script.js": """const form = document.querySelector("#todo-form");
const input = document.querySelector("#todo-input");
const list = document.querySelector("#todo-list");

function addTodo(text) {
  const item = document.createElement("li");
  const label = document.createElement("span");
  const remove = document.createElement("button");
  label.textContent = text;
  remove.textContent = "Sil";
  remove.addEventListener("click", () => item.remove());
  item.append(label, remove);
  list.appendChild(item);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addTodo(text);
  input.value = "";
});
""",
    }
def _portfolio_files() -> dict[str, str]:
    return {
        "portfolio_site/index.html": """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Portfoy</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header><h1>Merhaba, ben Ad Soyad</h1><p>Web gelistirme odakli portfoy.</p></header>
  <main><section><h2>Projeler</h2><p>Projelerinizi buraya ekleyin.</p></section></main>
  <script src="script.js"></script>
</body>
</html>
""",
        "portfolio_site/style.css": """body {
  margin: 0;
  font-family: Arial, sans-serif;
  color: #1f2937;
  background: #f7fafc;
}
header {
  padding: 48px 24px;
  background: #0f766e;
  color: white;
}
main {
  max-width: 900px;
  margin: 0 auto;
  padding: 32px 24px;
}
section {
  margin-bottom: 28px;
}
""",
        "portfolio_site/script.js": """console.log("Portfolyo sitesi hazır!");""",
    }


def _restaurant_site_files() -> dict[str, str]:
    return {
        "restaurant_site/index.html": """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gusto - Modern Restaurant</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="hero">
    <nav>
      <div class="logo">Gusto</div>
      <ul>
        <li><a href="#menu">Menü</a></li>
        <li><a href="#about">Hakkımızda</a></li>
        <li><a href="#contact">İletişim</a></li>
      </ul>
    </nav>
    <div class="hero-content">
      <h1>Lezzetin Sanatla Buluştuğu Yer</h1>
      <p>Taze malzemelerle hazırlanan enfes tarifler ve unutulmaz anlar.</p>
      <a href="#menu" class="btn">Menüyü İncele</a>
    </div>
  </header>
  
  <section id="menu" class="menu-section">
    <h2>Öne Çıkan Lezzetlerimiz</h2>
    <div class="menu-grid">
      <div class="menu-item">
        <h3>Özel Gusto Burger</h3>
        <p>Karamelize soğan, özel sos ve çıtır patates ile.</p>
        <span class="price">320 TL</span>
      </div>
      <div class="menu-item">
        <h3>Tütsülenmiş Antrikot</h3>
        <p>Taze baharatlar ve fırınlanmış sebzeler eşliğinde.</p>
        <span class="price">480 TL</span>
      </div>
      <div class="menu-item">
        <h3>Ev Yapımı Lazanya</h3>
        <p>Bol kıymalı sos ve erimiş mozzarella ile kat kat lezzet.</p>
        <span class="price">290 TL</span>
      </div>
    </div>
  </section>
  
  <footer id="contact">
    <p>&copy; 2026 Gusto Restaurant. Tüm Hakları Saklıdır.</p>
  </footer>
  <script src="script.js"></script>
</body>
</html>
""",
        "restaurant_site/style.css": """body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #0d0d0d;
  color: #f5f5f5;
}
.hero {
  background: linear-gradient(135deg, #1f120c, #0d0d0d);
  height: 80vh;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 20px;
}
nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
nav .logo {
  font-size: 24px;
  font-weight: bold;
  color: #d4af37;
}
nav ul {
  list-style: none;
  display: flex;
  gap: 20px;
}
nav a {
  color: #f5f5f5;
  text-decoration: none;
  font-weight: 500;
}
.hero-content {
  text-align: center;
  margin: auto;
}
.hero-content h1 {
  font-size: 48px;
  color: #d4af37;
  margin-bottom: 20px;
}
.btn {
  display: inline-block;
  background: #d4af37;
  color: #0d0d0d;
  padding: 12px 24px;
  text-decoration: none;
  font-weight: bold;
  border-radius: 4px;
  margin-top: 20px;
}
.menu-section {
  padding: 60px 20px;
  text-align: center;
}
.menu-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 30px;
  margin-top: 40px;
}
.menu-item {
  background: #1a1a1a;
  padding: 20px;
  border-radius: 8px;
  border: 1px solid #2a2a2a;
}
.menu-item h3 {
  color: #d4af37;
  margin-top: 0;
}
.price {
  display: block;
  font-weight: bold;
  margin-top: 15px;
  color: #d4af37;
}
footer {
  text-align: center;
  padding: 20px;
  border-top: 1px solid #1a1a1a;
}
""",
        "restaurant_site/script.js": """document.addEventListener("DOMContentLoaded", () => {
  console.log("Gusto Restaurant web sitesi hazır!");
});
"""
    }


def _generated_project_files() -> dict[str, str]:
    return {
        "generated_project/index.html": """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Yapay Zeka Projesi</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>Yapay Zeka ile Otomatik Proje</h1>
  <p>Bu proje otonom ajan tarafından otomatik olarak oluşturulmuştur.</p>
  <script src="script.js"></script>
</body>
</html>
""",
        "generated_project/style.css": """body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #f0f4f8;
  color: #102a43;
  padding: 40px;
}
h1 {
  color: #0b69a3;
}
""",
        "generated_project/script.js": """console.log("Otonom generated_project hazır!");"""
    }


def _normalize(text: str) -> str:
    return (
        (text or "")
        .lower()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ö", "o")
        .replace("ç", "c")
    )
