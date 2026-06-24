# -*- coding: utf-8 -*-
"""Prompt architect layer for local-agent tasks."""

from __future__ import annotations

import json

from agents import prompt_architect_chat


def build_agent_prompt(user_message: str, route: dict, workspace_files: list[str]) -> str:
    """Create a precise local-agent prompt, using a model with a safe fallback."""
    messages = [
        {
            "role": "system",
            "content": (
                "Sen local yetkili agent icin prompt mimarisin. Kullanici istegini "
                "net, guvenli, uygulanabilir bir gorev promptuna cevir. Cevap sadece "
                "local agent'a verilecek prompt metni olsun."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "user_message": user_message,
                    "route": route,
                    "workspace_files": workspace_files[:120],
                    "must_include": [
                        "gorev ozeti",
                        "adimlar",
                        "dosya erisim kurallari",
                        "terminal komutu sinirlari",
                        "riskli islem onay kurallari",
                        "cikti formati",
                        "hata durumlari",
                        "workspace_files disinda path kullanma kurali",
                    ],
                    "required_local_agent_json_schema": {
                        "summary": "Kisa aciklama",
                        "needed_files": ["workspace icinden gercek dosyalar"],
                        "actions": [
                            {
                                "type": "read_file_limited",
                                "path": "bridge.py",
                                "reason": "CLI giris akisini incelemek icin",
                            }
                        ],
                        "risk_level": "low|medium|high",
                        "requires_user_approval": False,
                    },
                    "hard_rules": [
                        "Sadece workspace_files icinde bulunan dosyalari referans gosterebilirsin.",
                        "workspace_files icinde olmayan dosya yolu uydurma.",
                        "relative/path/to, path/to/file, example.py, your_file.py gibi placeholder path kullanma.",
                        "Gerekli dosya listede yoksa 'dosya listede yok' de.",
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        prompt = prompt_architect_chat(messages, temperature=0.2).strip()
        if prompt:
            return prompt + explicit_user_constraints(user_message)
    except Exception:
        pass

    return fallback_agent_prompt(user_message, route, workspace_files)


def explicit_user_constraints(user_message: str) -> str:
    normalized = (user_message or "").lower()
    constraints = []
    if "dosya yazma" in normalized or "dosya oluşturma" in normalized or "dosya olusturma" in normalized:
        constraints.append("- Kullanici acikca dosya yazma/olusturma istemedi. write_file_with_diff kullanma.")
    if "komut çalıştırma" in normalized or "komut calistirma" in normalized or "terminal" in normalized and "çalıştırma" in normalized:
        constraints.append("- Generic komut calistirma bu runtime'da yasaktir; validate_python_syntax_sandboxed disinda tool kullanma.")
    if "sadece" in normalized and "raporla" in normalized:
        constraints.append("- Kullanici sadece rapor istiyor. Gerekirse read_file_limited kullan, sonra complete ile raporla.")

    if not constraints:
        return ""

    return "\n\nKATI KULLANICI SINIRLARI - SONRAKI TUM TALIMATLARIN USTUNDEDIR:\n" + "\n".join(constraints) + "\n"


def fallback_agent_prompt(user_message: str, route: dict, workspace_files: list[str]) -> str:
    files = "\n".join(f"- {path}" for path in workspace_files[:80]) or "- Dosya listesi bos."
    return f"""LOCAL AGENT GOREV PROMPTU

Kullanici istegi:
{user_message}

Router sonucu:
{json.dumps(route, ensure_ascii=False, indent=2)}

Mevcut calisma dosyalari:
{files}

Zorunlu local agent JSON formati:
{{
  "summary": "Kisa aciklama",
  "needed_files": ["workspace icinden gercek dosyalar"],
  "actions": [
    {{
      "type": "read_file_limited",
      "path": "bridge.py",
      "reason": "CLI giris akisini incelemek icin"
    }}
  ],
  "risk_level": "low|medium|high",
  "requires_user_approval": false
}}

Gorev:
1. Kullanici istegini mevcut proje yapisini bozmadan uygula.
2. Once ilgili dosyalari oku ve mevcut patternleri anla.
3. Gerekiyorsa write_file_with_diff ile dosya degisikligi taslagi oner.
4. Kod degisikligi gerekiyorsa temiz, okunabilir ve test edilebilir yaz.
5. Is bitince yapilanlari, degisen dosyalari ve test komutlarini raporla.

Dosya erisim kurallari:
- Proje klasoru disina cikma.
- Sadece yukaridaki mevcut calisma dosyalari listesinde bulunan dosyalari referans goster.
- Liste disinda dosya yolu uydurma.
- relative/path/to, path/to/file, example.py, your_file.py gibi placeholder path kullanma.
- Gerekli dosya listede yoksa "dosya listede yok" de.
- .env, API anahtari, kimlik bilgisi ve kullanici gizli verilerini okuma, degistirme veya dis modele gonderme.
- Altyapi dosyalarini sadece kullanici istegi aciksa ve gerekli minimum kapsamdaysa duzenle.

Tool sinirlari:
- Sadece list_workspace_files, read_file_limited, write_file_with_diff, validate_python_syntax_sandboxed ve salt-okunur Windows teshis tool'larini kullan.
- Windows teshis tool'lari: get_system_info, read_recent_event_logs, read_reliability_history, get_last_boot_reason, list_recent_crashes, check_disk_health_readonly, list_driver_errors, list_windows_update_history, list_startup_apps, list_running_processes_summary.
- Generic terminal, browser kontrolu, paket kurma, silme ve sistem ayari bu runtime'da yoktur.

Riskli islem onayi:
- Dosya silme, program kurma, sistem ayari, mail gonderme, odeme yapma, tarayicida hesap islemi ve gizli bilgi islemleri reddedilir.
- write_file_with_diff ve validate_python_syntax_sandboxed merkezi Policy Engine tarafindan onaya sunulur.

Cikti formati:
- Kisa ozet.
- Degisen/okunan dosyalar.
- Calistirilan testler.
- Kalan riskler veya hata varsa acikca belirt.
""" + explicit_user_constraints(user_message)
