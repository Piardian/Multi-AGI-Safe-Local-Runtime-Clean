# -*- coding: utf-8 -*-
"""Direct tool mapping layer to bypass LLM/browser brain for basic local tasks."""

from __future__ import annotations

import re


def extract_source_folder(text: str) -> str | None:
    t = text.lower().strip()
    if any(m in t for m in [
        "masaustunde", "masaustunden", "masaustundeki", 
        "desktopta", "desktop'ta", "desktop'taki", "desktopdaki", "desktop'dan", "desktopdan"
    ]):
        return "desktop"
    if any(m in t for m in [
        "belgelerde", "belgelerden", "belgelerdeki", "belgeler klasorunde", 
        "belgeler klasorunden", "belgeler klasorundeki", "documents'ta", "documents'taki", "documents'tan"
    ]):
        return "documents"
    if any(m in t for m in [
        "indirilenlerde", "indirilenlerden", "indirilenlerdeki", "indirilenler klasorunde", 
        "indirilenler klasorunden", "indirilenler klasorundeki", "downloads'ta", "downloads'taki", "downloads'tan"
    ]):
        return "downloads"
    return None


def extract_source_and_destination(text: str) -> tuple[str | None, str | None]:
    # 1. Identify source
    src_folder = None
    src_keyword = None
    
    # We list patterns in order of specificity (longest first)
    src_patterns = [
        ("belgeler klasorunden", "documents"),
        ("belgeler klasorunde", "documents"),
        ("belgeler klasorundeki", "documents"),
        ("indirilenler klasorunden", "downloads"),
        ("indirilenler klasorunde", "downloads"),
        ("indirilenler klasorundeki", "downloads"),
        ("masaustunden", "desktop"),
        ("masaustunde", "desktop"),
        ("masaustundeki", "desktop"),
        ("belgelerden", "documents"),
        ("belgelerdeki", "documents"),
        ("belgelerde", "documents"),
        ("indirilenlerden", "downloads"),
        ("indirilenlerdeki", "downloads"),
        ("indirilenlerde", "downloads"),
        ("desktop'taki", "desktop"),
        ("desktop'taki", "desktop"),
        ("desktop'tan", "desktop"),
        ("desktop'ta", "desktop"),
        ("desktopdan", "desktop"),
        ("desktopta", "desktop"),
        ("documents'taki", "documents"),
        ("documents'tan", "documents"),
        ("documents'ta", "documents"),
        ("downloads'taki", "downloads"),
        ("downloads'tan", "downloads"),
        ("downloads'ta", "downloads"),
    ]
    
    for pat, folder in src_patterns:
        if pat in text:
            src_folder = folder
            src_keyword = pat
            break
            
    # Remove the matched source keyword from the text so it won't interfere with destination detection
    text_for_dest = text
    if src_keyword:
        text_for_dest = text.replace(src_keyword, "")
        
    # 2. Identify destination
    dst_folder = None
    dst_patterns = [
        ("belgeler klasorune", "documents"),
        ("belgeler klasorune", "documents"),
        ("belgeler klasorundeki", "documents"),
        ("indirilenler klasorune", "downloads"),
        ("indirilenler klasorune", "downloads"),
        ("proje klasorune", "workspace"),
        ("workspace'e", "workspace"),
        ("workspacea", "workspace"),
        ("belgelere", "documents"),
        ("documents'a", "documents"),
        ("documentsa", "documents"),
        ("masaustune", "desktop"),
        ("desktop'a", "desktop"),
        ("desktopa", "desktop"),
        ("indirilenlere", "downloads"),
        ("downloads'a", "downloads"),
        ("downloadsa", "downloads"),
        ("proje klasoru", "workspace"),
        ("proje", "workspace"),
    ]
    
    for pat, folder in dst_patterns:
        if pat in text_for_dest:
            dst_folder = folder
            break
            
    return src_folder, dst_folder


def try_direct_map(goal: str) -> list[dict] | None:
    from router import _normalize
    text = _normalize(goal)

    # 1. open_application mappings (Notepad, Calculator)
    is_notepad = (
        ("not defteri" in text or "notepad" in text)
        and ("ac" in text or "baslat" in text or "open" in text or "start" in text)
    )
    if is_notepad:
        return [{
            "tool": "open_application",
            "app": "notepad",
            "reason": "Directly mapped open_application for notepad."
        }]

    is_calc = (
        ("hesap makinesi" in text or "calc" in text or "calculator" in text)
        and ("ac" in text or "baslat" in text or "open" in text or "start" in text)
    )
    if is_calc:
        return [{
            "tool": "open_application",
            "app": "calc",
            "reason": "Directly mapped open_application for calc."
        }]

    # 2. Windows crash logs mapping
    is_crash = (
        ("cokme" in text or "crash" in text or "event log" in text or "olay gunlugu" in text)
        and ("goster" in text or "listele" in text or "oku" in text or "son" in text)
    )
    if is_crash:
        return [{
            "tool": "list_recent_crashes",
            "reason": "Directly mapped list_recent_crashes action."
        }]

    # 3. open_folder desktop mapping
    if "masaustunu ac" in text or "masaustu ac" in text:
        return [{
            "tool": "open_folder",
            "path": "desktop",
            "reason": "Directly mapped open_folder desktop."
        }]

    def extract_destination_folder(folder_str: str) -> str:
        f = folder_str.lower().strip()
        if "belge" in f or "document" in f:
            return "documents"
        if "masaustu" in f or "desktop" in f:
            return "desktop"
        if "indirilen" in f or "download" in f:
            return "downloads"
        if "workspace" in f or "proje klasor" in f or "proje" in f:
            return "workspace"
        return f

    # 4. Generic copy: <filename> dosyasını <folder> klasörüne kopyala
    if "kopyala" in text:
        fn_match = re.search(r'([a-zA-Z0-9_\-\.]+)\s*dosyasini', text) or re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', text)
        if fn_match:
            filename = fn_match.group(1).strip()
            src_folder, dest_folder = extract_source_and_destination(text)
            
            if not dest_folder:
                return [{
                    "tool": "copy_file",
                    "src": f"{src_folder}/{filename}" if src_folder else filename,
                    "dst": None,
                    "error": "missing_destination",
                    "filename": filename,
                    "operation": "kopyala",
                    "reason": f"Destination folder is missing for copy of {filename}."
                }]
                
            src_path = f"{src_folder}/{filename}" if src_folder else filename
            dst_path = f"{dest_folder}/{filename}" if dest_folder else filename
            return [{
                "tool": "copy_file",
                "src": src_path,
                "dst": dst_path,
                "reason": f"Directly mapped copy_file for {filename}."
            }]

    # 5. Generic move: <filename> dosyasını <folder> klasörüne taşı
    if "tasi" in text:
        fn_match = re.search(r'([a-zA-Z0-9_\-\.]+)\s*dosyasini', text) or re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', text)
        if fn_match:
            filename = fn_match.group(1).strip()
            src_folder, dest_folder = extract_source_and_destination(text)
            
            if not dest_folder:
                return [{
                    "tool": "move_file",
                    "src": f"{src_folder}/{filename}" if src_folder else filename,
                    "dst": None,
                    "error": "missing_destination",
                    "filename": filename,
                    "operation": "tasi",
                    "reason": f"Destination folder is missing for move of {filename}."
                }]
                
            src_path = f"{src_folder}/{filename}" if src_folder else filename
            dst_path = f"{dest_folder}/{filename}" if dest_folder else filename
            return [{
                "tool": "move_file",
                "src": src_path,
                "dst": dst_path,
                "reason": f"Directly mapped move_file for {filename}."
            }]

    # 6. Generic delete: <filename> dosyasını sil
    if "sil" in text:
        fn_match = re.search(r'([a-zA-Z0-9_\-\.]+)\s*dosyasini', text) or re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', text)
        if fn_match:
            filename = fn_match.group(1).strip()
            src_folder = extract_source_folder(text)
            path = f"{src_folder}/{filename}" if src_folder else filename
            return [{
                "tool": "safe_delete_file",
                "path": path,
                "reason": f"Directly mapped safe_delete_file for {filename}."
            }]

    # 7. Generic search: Masaüstünde <filename> ara / Masaüstünde <filename> dosyasını ara
    if "ara" in text or "arat" in text or "bul" in text:
        if "dosya" in text or re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', text):
            fn_match = re.search(r'([a-zA-Z0-9_\-\.]+)\s*dosya', text) or re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', text)
            if fn_match:
                filename = fn_match.group(1).strip()
                dest_search = text.replace(filename, "").replace("dosyasini", "").replace("dosyasi", "").replace("dosya", "").replace("ara", "").replace("bul", "").strip()
                dest_folder = extract_destination_folder(dest_search)
                if not dest_folder or dest_folder == text.strip():
                    dest_folder = "desktop"
                return [{
                    "tool": "search_files",
                    "query": filename,
                    "path": dest_folder,
                    "reason": f"Directly mapped search_files for {filename}."
                }]

    # 8. Generic file info: <filename> dosya bilgilerini göster
    if "bilgi" in text and ("goster" in text or "oku" in text or "getir" in text):
        fn_match = re.search(r'([a-zA-Z0-9_\-\.]+)\s*dosya', text) or re.search(r'([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', text)
        if fn_match:
            filename = fn_match.group(1).strip()
            src_folder = extract_source_folder(text)
            path = f"{src_folder}/{filename}" if src_folder else filename
            return [{
                "tool": "get_file_info",
                "path": path,
                "reason": f"Directly mapped get_file_info for {filename}."
            }]

    # 9. create_directory mapping
    is_create_dir = ("klasor" in text or "dizin" in text) and ("olustur" in text or "yap" in text)
    if is_create_dir:
        is_desktop = "masaustu" in text or "desktop" in text
        location = "desktop" if is_desktop else "workspace"
        
        folder_match = re.search(
            r'([a-zA-Z0-9_\-\.\/]+)\s*(?:adli|isimli)?\s*(?:klasor|dizin)(?:u|i|nu|ni)?\s*(?:olustur|yap)',
            text
        ) or re.search(
            r'(?:klasor|dizin)\s*(?:olustur(?:mak)?|yap(?:mak)?)\s*(?:adi|ismi)?\s*[:\s]+([a-zA-Z0-9_\-\.\/]+)',
            text
        )
        
        folder_name = "generated_folder"
        if folder_match:
            folder_name = folder_match.group(1).strip()
            folder_name = folder_name.rstrip(".,;:")
            
        if folder_name.lower() in {"masaustu", "masaustunde", "desktop", "workspace", "klasor", "dizin", "olustur", "yeni"}:
            folder_name = "yeni_klasor"
            
        return [{
            "tool": "create_directory",
            "path": folder_name,
            "location": location,
            "reason": f"Directly mapped create_directory action in {location}."
        }]

    # 10. write_file_with_diff mapping: create simple text files
    is_create_file = (
        ("olustur" in text or "yarat" in text or "yaz" in text)
        and ("dosya" in text or re.search(r'[a-zA-Z0-9_\-]+\.\w{1,5}', text))
        and not ("klasor" in text or "dizin" in text)
    )
    if is_create_file:
        # Extract filename
        fn_match = (
            re.search(r'([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,5})\s*(?:dosya|adli|isimli|adinda)', text)
            or re.search(r'(?:dosya|dosyasi)\s*(?:olustur|yarat)\s*(?:adi|ismi)?\s*:?\s*([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,5})', text)
            or re.search(r'([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,5})', text)
        )
        if fn_match:
            filename = fn_match.group(1).strip()
            dest_folder = extract_source_folder(text)
            if not dest_folder:
                dest_folder = "desktop"  # Default to desktop

            # Extract content if specified ("içine X yaz" or "içeriği X")
            content = ""
            content_match = (
                re.search(r'icine\s+(.+?)(?:\s+yaz|\s*$)', text)
                or re.search(r'icerigi?\s+(.+?)(?:\s*$)', text)
                or re.search(r'yaz(?:dir|mak)?\s*[:\s]+(.+?)$', text)
            )
            if content_match:
                # Use the original goal to preserve Turkish chars for content
                raw_goal = goal.lower()
                raw_content_match = (
                    re.search(r'içine\s+(.+?)(?:\s+yaz|\s*$)', raw_goal)
                    or re.search(r'içeriği?\s+(.+?)(?:\s*$)', raw_goal)
                    or re.search(r'yaz(?:dır|mak)?\s*[:\s]+(.+?)$', raw_goal)
                )
                if raw_content_match:
                    content = raw_content_match.group(1).strip()
                else:
                    content = content_match.group(1).strip()

            file_path = f"{dest_folder}/{filename}"
            return [{
                "tool": "write_file_with_diff",
                "path": file_path,
                "content": content or f"Bu dosya otomatik olarak oluşturulmuştur: {filename}",
                "reason": f"Directly mapped write_file_with_diff for {filename} in {dest_folder}."
            }]

    # 11. open_browser mapping
    is_open_browser = (
        ("chrome" in text or "tarayici" in text or "browser" in text)
        and ("ac" in text or "baslat" in text or "open" in text or "start" in text)
    )
    if is_open_browser:
        return [{
            "tool": "open_browser",
            "reason": "Directly mapped open_browser action."
        }]

    # 11. list_workspace_files mapping
    analysis_keywords = ["incele", "analiz", "sorun", "hata", "rapor", "plan", "duzelt"]
    has_analysis_intent = any(kw in text for kw in analysis_keywords)
    
    is_list_files = False
    if not has_analysis_intent:
        simple_listing_phrases = [
            "dosyalari listele",
            "dosya listele",
            "klasorde ne var",
            "workspace dosyalari",
            "dosyalarini goster",
            "dosyalari goster",
            "dosya goster",
            "workspace listele",
            "dizini listele"
        ]
        if any(phrase in text for phrase in simple_listing_phrases):
            is_list_files = True

    if is_list_files:
        return [{
            "tool": "list_workspace_files",
            "reason": "Directly mapped list_workspace_files action."
        }]

    # 12. get_system_info mapping
    is_sys_info = (
        ("bilgisayar" in text or "sistem" in text or "system" in text)
        and ("bilgi" in text or "ozellik" in text or "info" in text or "status" in text)
    )
    if is_sys_info:
        return [{
            "tool": "get_system_info",
            "reason": "Directly mapped get_system_info action."
        }]

    # 12.5. Remediation mappings (close, restart, log archive, config backup, cache clear)
    if "kapat" in text or "sonlandir" in text or "durdur" in text:
        match = re.search(r"([a-zA-Z0-9_\-\s]+?)(?:'i|'ı|'u|'ü|i|ı|u|ü)?\s+(?:kapat|sonlandir|durdur)", text)
        if match:
            app_name = match.group(1).strip()
            if app_name and app_name not in {"pencereyi", "tarayiciyi", "bilgisayari", "dosyayi"}:
                return [{
                    "tool": "close_application_process",
                    "app": app_name,
                    "reason": f"Directly mapped close_application_process for {app_name}."
                }]

    if "yeniden baslat" in text or "restart" in text:
        match = re.search(r"([a-zA-Z0-9_\-\s]+?)(?:'i|'ı|'u|'ü|i|ı|u|ü)?\s+(?:yeniden baslat|restart)", text)
        if match:
            app_name = match.group(1).strip()
            if app_name and app_name not in {"bilgisayari"}:
                return [{
                    "tool": "restart_application_resolved",
                    "app": app_name,
                    "reason": f"Directly mapped restart_application_resolved for {app_name}."
                }]

    if "arsivle" in text or "loglari yedekle" in text:
        match = re.search(r"([a-zA-Z0-9_\-\s]+?)(?:'in|'ın|'un|'ün|in|ın|un|ün)?\s+(?:loglerini arsivle|loglari arsivle|log arsivle|arsivle|loglari yedekle)", text)
        if match:
            app_name = match.group(1).strip()
            if app_name.endswith(" log") or app_name.endswith(" loglar"):
                app_name = app_name.rsplit(" ", 1)[0].strip()
            return [{
                "tool": "archive_application_logs",
                "app": app_name,
                "reason": f"Directly mapped archive_application_logs for {app_name}."
            }]

    if "config yedekle" in text or "konfigurasyon yedekle" in text or "config'i yedekle" in text or "ayarlarini yedekle" in text:
        match = re.search(r"([a-zA-Z0-9_\-\s]+?)(?:'in|'ın|'un|'ün|in|ın|un|ün)?\s+(?:config yedekle|konfigurasyon yedekle|config'i yedekle|config yedegi al|ayarlarini yedekle)", text)
        if match:
            app_name = match.group(1).strip()
            if app_name.endswith(" config") or app_name.endswith(" ayarlar"):
                app_name = app_name.rsplit(" ", 1)[0].strip()
            return [{
                "tool": "backup_application_config",
                "app": app_name,
                "reason": f"Directly mapped backup_application_config for {app_name}."
            }]

    if "cache temizle" in text or "onbellek temizle" in text or "cache'ini temizle" in text:
        match = re.search(r"([a-zA-Z0-9_\-\s]+?)(?:'in|'ın|'un|'ün|in|ın|un|ün)?\s+(?:cache temizle|onbellek temizle|cache'ini temizle)", text)
        if match:
            app_name = match.group(1).strip()
            if app_name.endswith(" cache") or app_name.endswith(" onbellek"):
                app_name = app_name.rsplit(" ", 1)[0].strip()
            return [{
                "tool": "clear_safe_application_cache",
                "app": app_name,
                "reason": f"Directly mapped clear_safe_application_cache for {app_name}."
            }]

    # 13. General App Launch Mapping
    # Matches "X ac", "steam'den X ac", "open X", "start X", etc.
    launch_match = (
        re.search(r"steam'den\s+([a-zA-Z0-9_\-\s]+)\s+ac", text) or
        re.search(r"steamden\s+([a-zA-Z0-9_\-\s]+)\s+ac", text) or
        re.search(r"steam den\s+([a-zA-Z0-9_\-\s]+)\s+ac", text) or
        re.search(r"([a-zA-Z0-9_\-\s]+)\s+ac", text) or
        re.search(r"([a-zA-Z0-9_\-\s]+)\s+calistir", text) or
        re.search(r"([a-zA-Z0-9_\-\s]+)\s+baslat", text) or
        re.search(r"open\s+([a-zA-Z0-9_\-\s]+)", text) or
        re.search(r"start\s+([a-zA-Z0-9_\-\s]+)", text)
    )
    if launch_match:
        app_name = launch_match.group(1).strip()
        # Filter out common false positives and specific targets
        if app_name and app_name not in {
            "not defteri", "hesap makinesi", "chrome", "tarayici", "browser",
            "masaustu", "masaustunu", "dosyalari", "dosya", "klasor", "klasorde", "dizini", "workspace"
        }:
            # Handle Counter-Strike mapping
            if any(cs_term in app_name.lower() for cs_term in ["cs2", "counter-strike", "counter strike", "counterstrike"]):
                app_name = "Counter-Strike 2"
            return [{
                "tool": "launch_application_resolved",
                "app": app_name,
                "reason": f"Directly mapped launch_application_resolved for {app_name}."
            }]

    # 14. Application Troubleshooting/Diagnostics Mapping
    # Matches "X acilmiyor", "X dondu", "X takildi", etc.
    trouble_keywords = ["acilmiyor", "calismiyor", "takildi", "dondu", "hata veriyor", "sorun ne", "hatali", "coke"]
    if any(kw in text for kw in trouble_keywords):
        trouble_match = re.search(r"([a-zA-Z0-9_\-\s]+?)\s+(?:acilmiyor|calismiyor|dondu|takildi|hata|sorun|coke)", text)
        if trouble_match:
            app_name = trouble_match.group(1).strip().split()[0]
            if app_name and app_name not in {"bu", "neden", "sistem", "bilgisayar"}:
                return [{
                    "tool": "application_diagnostics",
                    "app": app_name,
                    "reason": f"Directly mapped application_diagnostics for {app_name}."
                }]
        # Fallback to first word if keywords exist
        first_word = text.split()[0].strip()
        if first_word and first_word not in {"bu", "neden", "sistem", "bilgisayar", "klasor", "dosya"}:
            return [{
                "tool": "application_diagnostics",
                "app": first_word,
                "reason": f"Directly mapped application_diagnostics for {first_word}."
            }]

    return None
