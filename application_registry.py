# -*- coding: utf-8 -*-
"""Application Registry and Discovery Module for Sprint 11."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
import config
from data_policy import is_sensitive_path, redact_text

# Global mock toggle for testing
MOCK_REGISTRY = {}
IS_MOCK_MODE = False
USE_REAL_STEAM_FALLBACK = False

# Allowed URI schemes
ALLOWED_URI_SCHEMES = ("steam://", "com.epicgames.launcher://", "https://")
BLOCKED_URI_SCHEMES = ("file://", "powershell:", "cmd:", "javascript:", "ms-appinstaller:")


def is_protected_system_path(path_str: str) -> bool:
    """Check if the target path is in a protected system directory."""
    p = os.path.abspath(path_str).lower()
    
    # If the path starts with Windows directory, check if it's notepad.exe or calc.exe
    win_dir = os.environ.get('SystemRoot', 'C:\\Windows').lower()
    if p.startswith(win_dir):
        basename = os.path.basename(p)
        if basename in {"notepad.exe", "calc.exe", "notepad", "calc"}:
            return False
        return True
        
    # Block workspace sensitive dirs
    for folder in [".git", ".venv", "node_modules"]:
        if folder in p.split(os.sep):
            return True
    return False


def parse_lnk_file(filepath: str) -> str | None:
    """Parse binary .lnk Shell Link files to extract their target path without pywin32."""
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
        if len(data) < 76:
            return None
        # Signature check
        if data[:4] != b'\x4c\x00\x00\x00':
            return None
        
        # LinkFlags (offset 20, 4 bytes)
        flags = int.from_bytes(data[20:24], byteorder='little')
        has_id_list = bool(flags & 0x01)
        has_link_info = bool(flags & 0x02)
        
        offset = 76
        if has_id_list:
            if len(data) < offset + 2:
                return None
            id_list_size = int.from_bytes(data[offset:offset+2], byteorder='little')
            offset += 2 + id_list_size
            
        if not has_link_info:
            return None
            
        if len(data) < offset + 28:
            return None
            
        link_info_start = offset
        local_base_path_offset = int.from_bytes(data[link_info_start+16:link_info_start+20], byteorder='little')
        
        path_offset = link_info_start + local_base_path_offset
        if path_offset < len(data):
            end_offset = data.find(b'\x00', path_offset)
            if end_offset != -1:
                path_bytes = data[path_offset:end_offset]
                try:
                    resolved_path = path_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        resolved_path = path_bytes.decode('mbcs')
                    except Exception:
                        resolved_path = path_bytes.decode('latin1')
                
                # Expand environment variables if any
                return os.path.expandvars(resolved_path)
        return None
    except Exception:
        return None


def validate_alias_target(target: str, launch_type: str) -> tuple[bool, str]:
    """Validate alias target against path injection, command executors, and scheme allowlist."""
    target_clean = target.strip()
    
    # 1. Path/exe injection check
    if any(c in target_clean for c in ['&', '|', ';', '$', '>', '<', '`', '\n']):
        return False, "Command injection character detected"
        
    target_lower = target_clean.lower()
    
    # 2. Blocked URI schemes check
    if any(target_lower.startswith(scheme) for scheme in BLOCKED_URI_SCHEMES):
        return False, "Blocked URI scheme detected"

    # 3. cmd/powershell/shell target check
    if "cmd.exe" in target_lower or "powershell.exe" in target_lower or "cmd " in target_lower or "powershell " in target_lower:
        return False, "Target contains cmd or powershell"
        
    # 3. launch_type scheme allowlist
    if launch_type == "steam_uri":
        if not target_lower.startswith("steam://"):
            return False, "Steam URI must start with steam://"
        if any(scheme in target_lower for scheme in BLOCKED_URI_SCHEMES):
            return False, "Blocked URI scheme detected"
    elif launch_type == "epic_uri":
        if not target_lower.startswith("com.epicgames.launcher://"):
            return False, "Epic URI must start with com.epicgames.launcher://"
        if any(scheme in target_lower for scheme in BLOCKED_URI_SCHEMES):
            return False, "Blocked URI scheme detected"
    elif launch_type == "browser_url":
        if not target_lower.startswith("https://"):
            return False, "Browser URL must start with https://"
        if any(scheme in target_lower for scheme in BLOCKED_URI_SCHEMES):
            return False, "Blocked URI scheme detected"
    elif launch_type == "shortcut":
        # Check script engine/file blocks
        if any(engine in target_lower for engine in ["wscript", "cscript", "mshta", "rundll32", "regsvr32"]):
            return False, "Target contains blocked script or shell launcher"
        if target_lower.endswith((".bat", ".cmd", ".ps1", ".vbs", ".js")):
            return False, "Target cannot be a script file"
        if is_protected_system_path(target_clean):
            return False, "Target is inside a protected system path"
    else:
        return False, f"Invalid or disallowed launch_type: {launch_type}"
        
    return True, ""


def init_default_aliases():
    """Create default template for app_aliases.json if not present."""
    alias_dir = os.path.join(config.PROJECT_ROOT, "config")
    os.makedirs(alias_dir, exist_ok=True)
    alias_file = os.path.join(alias_dir, "app_aliases.json")
    
    if not os.path.exists(alias_file):
        default_data = {
            "counter-strike 2": {
                "display_name": "Counter-Strike 2",
                "aliases": ["cs2", "counter-strike", "counter strike"],
                "launch_type": "steam_uri",
                "launch_target": "steam://rungameid/730"
            },
            "codex": {
                "display_name": "Codex",
                "aliases": ["codex"],
                "launch_type": "shortcut",
                "launch_target": "C:\\Program Files\\Codex\\Codex.exe",
                "log_path": "C:\\Program Files\\Codex\\logs",
                "config_path": "C:\\Program Files\\Codex\\config.json",
                "cache_path": "C:\\Users\\User\\AppData\\Local\\Codex\\cache"
            }
        }
        with open(alias_file, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=4, ensure_ascii=False)


def load_aliases() -> dict:
    """Load config/app_aliases.json safely."""
    init_default_aliases()
    alias_file = os.path.join(config.PROJECT_ROOT, "config", "app_aliases.json")
    try:
        with open(alias_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def discover_applications() -> list[dict]:
    """Perform read-only discovery of installed applications on the system.

    Strictly:
    - Does not open applications
    - Does not execute shortcuts/exes
    - Does not trigger URIs
    - Does not make system changes
    """
    if IS_MOCK_MODE:
        return list(MOCK_REGISTRY.values())

    discovered = {}
    
    # 1. Parse Steam manifests
    steam_paths = [
        "C:\\Program Files (x86)\\Steam\\steamapps",
        "C:\\Program Files\\Steam\\steamapps"
    ]
    for sp in steam_paths:
        if os.path.isdir(sp):
            for file in os.listdir(sp):
                if file.startswith("appmanifest_") and file.endswith(".acf"):
                    acf_path = os.path.join(sp, file)
                    try:
                        with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        appid_match = re.search(r'"appid"\s+"(\d+)"', content)
                        name_match = re.search(r'"name"\s+"([^"]+)"', content)
                        if appid_match and name_match:
                            appid = appid_match.group(1)
                            name = name_match.group(1)
                            key = name.lower()
                            discovered[key] = {
                                "display_name": name,
                                "aliases": [name.lower(), f"steam_{appid}"],
                                "source": "steam",
                                "launch_type": "steam_uri",
                                "launch_target": f"steam://rungameid/{appid}",
                                "confidence": 1.0,
                                "verified": True,
                                "requires_approval": True,
                                "risk_level": "high"
                            }
                    except Exception:
                        pass

    # 2. Scan Start Menu and Desktop shortcuts
    shortcut_dirs = [
        "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs",
        os.path.expandvars("%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs"),
        os.path.expandvars("%USERPROFILE%\\Desktop"),
        "C:\\Users\\Public\\Desktop"
    ]
    for sd in shortcut_dirs:
        if os.path.isdir(sd):
            for root, _, files in os.walk(sd):
                for f in files:
                    if f.endswith(".lnk"):
                        lnk_path = os.path.join(root, f)
                        target = parse_lnk_file(lnk_path)
                        app_name = os.path.splitext(f)[0]
                        key = app_name.lower()
                        
                        source_type = "desktop" if "Desktop" in root else "start_menu"
                        
                        if target:
                            # Verify target safety
                            is_valid, err = validate_alias_target(target, "shortcut")
                            is_sensitive = is_sensitive_path(target) or (redact_text(target) != target)
                            
                            if is_valid and not is_sensitive:
                                discovered[key] = {
                                    "display_name": app_name,
                                    "aliases": [app_name.lower()],
                                    "source": source_type,
                                    "launch_type": "shortcut",
                                    "launch_target": target,
                                    "confidence": 1.0,
                                    "verified": True,
                                    "requires_approval": True,
                                    "risk_level": "medium"
                                }
                            else:
                                # Target safety check failed or unresolved
                                discovered[key] = {
                                    "display_name": app_name,
                                    "aliases": [app_name.lower()],
                                    "source": source_type,
                                    "launch_type": "shortcut",
                                    "launch_target": target or "",
                                    "confidence": 0.2,
                                    "verified": False,
                                    "requires_approval": True,
                                    "risk_level": "high"
                                }
                        else:
                            # Target unresolved
                            discovered[key] = {
                                "display_name": app_name,
                                "aliases": [app_name.lower()],
                                "source": source_type,
                                "launch_type": "shortcut",
                                "launch_target": "",
                                "confidence": 0.2,
                                "verified": False,
                                "requires_approval": True,
                                "risk_level": "high"
                            }

    # 3. Program Files metadata check (for metadata signals only)
    program_files_dirs = [
        "C:\\Program Files",
        "C:\\Program Files (x86)"
    ]
    for pfd in program_files_dirs:
        if os.path.isdir(pfd):
            try:
                for entry in os.listdir(pfd):
                    entry_path = os.path.join(pfd, entry)
                    if os.path.isdir(entry_path):
                        key = entry.lower()
                        # If already discovered via Steam or Shortcut, don't downgrade it to metadata
                        if key not in discovered:
                            discovered[key] = {
                                "display_name": entry,
                                "aliases": [entry.lower()],
                                "source": "metadata",
                                "launch_type": "shortcut",
                                "launch_target": "",  # Empty target, cannot launch directly from metadata
                                "confidence": 0.3,
                                "verified": False,
                                "requires_approval": True,
                                "risk_level": "high"
                            }
            except Exception:
                pass

    # 4. Load User Aliases and merge
    aliases_data = load_aliases()
    for alias_key, val in aliases_data.items():
        name = val.get("display_name", alias_key)
        target = val.get("launch_target", "")
        launch_type = val.get("launch_type", "shortcut")
        
        # Check safety
        is_valid, err = validate_alias_target(target, launch_type)
        is_sensitive = is_sensitive_path(target) or (redact_text(target) != target)
        
        if not is_valid or is_sensitive:
            # Reject alias if safety checks fail
            continue
            
        # Determine verified status: verified ONLY if disk discovery matches the item
        # E.g. Steam app found on disk or Shortcut target found
        is_verified = False
        
        # Check if matched in discovered items
        matched_on_disk = False
        for disc_val in discovered.values():
            if disc_val.get("source") in ("steam", "start_menu", "desktop") and disc_val.get("verified"):
                if launch_type == "steam_uri" and disc_val.get("launch_target") == target:
                    matched_on_disk = True
                    break
                if launch_type == "shortcut" and disc_val.get("launch_target") and os.path.abspath(disc_val.get("launch_target")).lower() == os.path.abspath(target).lower():
                    matched_on_disk = True
                    break
                    
        if matched_on_disk:
            is_verified = True
        elif launch_type == "browser_url":
            # Browser URLs don't reside on disk, but we check if they are in ALLOWED_URI_SCHEMES
            is_verified = target.lower().startswith("https://")
            
        key = alias_key.lower()
        # Merge, but preserve disk discovery if it's already verified and exists
        if key in discovered and discovered[key].get("verified"):
            # Existing verified disk item takes precedence, but we can append aliases
            for a in val.get("aliases", []):
                if a not in discovered[key]["aliases"]:
                    discovered[key]["aliases"].append(a)
            discovered[key]["log_path"] = val.get("log_path", "")
            discovered[key]["config_path"] = val.get("config_path", "")
            discovered[key]["cache_path"] = val.get("cache_path", "")
        else:
            discovered[key] = {
                "display_name": name,
                "aliases": val.get("aliases", [alias_key]),
                "source": "alias",
                "launch_type": launch_type,
                "launch_target": target,
                "confidence": 0.8 if is_verified else 0.4,
                "verified": is_verified,
                "requires_approval": True,
                "risk_level": "medium" if is_verified else "high",
                "log_path": val.get("log_path", ""),
                "config_path": val.get("config_path", ""),
                "cache_path": val.get("cache_path", "")
            }

    # Ensure all discovered items have log_path, config_path, cache_path keys
    for key in discovered:
        discovered[key].setdefault("log_path", "")
        discovered[key].setdefault("config_path", "")
        discovered[key].setdefault("cache_path", "")

    return list(discovered.values())


def match_application(query: str) -> dict | None:
    """Find matching application in registry using exact match or alias list."""
    apps = discover_applications()
    q = query.strip().lower()
    
    # Exact or alias list match
    for app in apps:
        if q == app["display_name"].lower():
            return app
        for alias in app.get("aliases", []):
            if q == alias.lower():
                return app
                
    # Partial match
    for app in apps:
        if q in app["display_name"].lower():
            return app
        for alias in app.get("aliases", []):
            if q in alias.lower():
                return app
                
    return None
