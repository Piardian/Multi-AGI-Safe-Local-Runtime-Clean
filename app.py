# -*- coding: utf-8 -*-
"""Interactive Orchestrator CLI Shell Entrypoint."""

import sys
import os
import logging
import datetime
import config

# Adjust logging configuration if we are in interactive mode and debug is off
def setup_interactive_logging(debug: bool = False):
    logger = logging.getLogger()
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            if debug:
                handler.setLevel(logging.INFO)
            else:
                handler.setLevel(logging.WARNING)

# Session context stored only in memory (RAM)
SESSION_CONTEXT = []

def run_interactive_shell():
    # Set config flag
    config.INTERACTIVE_MODE = True
    
    # Enable defaults/loads
    from tools.registry import load_default_tools
    load_default_tools()
    
    import bridge
    from data_policy import redact_value
    from router import classify_message
    
    debug_mode = False
    setup_interactive_logging(debug_mode)
    
    print("========================================================================+")
    print("           ANTIGRAVITY PERSONAL ORCHESTRATOR")
    print("========================================================================+")
    print("Komut yazın. Çıkmak için: exit")
    print("Yardım için: /help")
    print()
    
    while True:
        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nÇıkış yapılıyor...")
            break
            
        if not user_input:
            continue
            
        user_input = user_input.replace("\ufeff", "").replace("ï»¿", "")
        
        if not user_input:
            continue
            
        if user_input.lower() in {"exit", "quit", "/exit", "/quit"}:
            print("Güle güle!")
            break
            
        # Parse slash commands
        if user_input.startswith("/"):
            parts = user_input.split(" ", 1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            
            try:
                if cmd == "/help":
                    print_help_menu()
                elif cmd == "/tools":
                    print_tool_catalog()
                elif cmd == "/debug":
                    if arg.lower() == "on":
                        debug_mode = True
                        setup_interactive_logging(True)
                        print("Debug modu AÇIK.")
                    elif arg.lower() == "off":
                        debug_mode = False
                        setup_interactive_logging(False)
                        print("Debug modu KAPALI.")
                    else:
                        print(f"Bilinmeyen debug parametresi: '{arg}'. Kullanım: /debug [on|off]")
                elif cmd == "/memory":
                    # Parse arg e.g. "last 3" or "search pattern"
                    sub_parts = arg.split(" ", 1)
                    sub_cmd = sub_parts[0].lower() if len(sub_parts) > 0 else ""
                    sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""
                    
                    if sub_cmd == "last":
                        try:
                            n = int(sub_arg) if sub_arg else 5
                        except ValueError:
                            n = 5
                        import task_memory
                        task_memory.print_last_runs(n)
                    elif sub_cmd == "search":
                        if not sub_arg:
                            print("Arama terimi girin. Örn: /memory search Chrome")
                        else:
                            import task_memory
                            task_memory.search_runs(sub_arg)
                    else:
                        print("Geçersiz /memory komutu. Örn: /memory last 3 veya /memory search Chrome")
                elif cmd == "/providers":
                    print_providers_info()
                elif cmd == "/health":
                    run_safe_health_check()
                elif cmd == "/brain":
                    if arg.lower() == "health":
                        print("Browser brain test ediliyor...")
                        from bridge import run_brain_health_check
                        run_brain_health_check()
                    else:
                        print("Bilinmeyen /brain parametresi: '{arg}'. Kullanım: /brain health")
                elif cmd == "/benchmark":
                    if arg.lower() == "local":
                        from bridge import run_local_model_benchmark
                        run_local_model_benchmark()
                    else:
                        print("Bilinmeyen /benchmark parametresi: '{arg}'. Kullanım: /benchmark local")
                elif cmd == "/route":
                    if not arg:
                        print("Yönlendirilecek bir hedef girin. Örn: /route Chrome'u aç")
                    else:
                        res = bridge.resolve_goal_route(arg, preview_only=True)
                        print(f"Kategori: {res['category']}")
                        print(f"Direct map: {'true' if res['direct_map'] else 'false'}")
                        print(f"Tools: {', '.join(res['tools']) if res['tools'] else 'None'}")
                        print(f"Risk: {res['risk']}")
                elif cmd == "/plan":
                    if not arg:
                        print("Planlanacak bir hedef girin. Örn: /plan Masaüstünde test klasörü oluştur")
                    else:
                        bridge.run_bridge_autonomous_loop(
                            arg,
                            plan_only=True,
                            debug=debug_mode
                        )
                else:
                    print(f"Bilinmeyen komut: {cmd}. Yardım için /help yazın.")
            except Exception as e:
                print(f"[HATA] Komut çalıştırılırken sorun oluştu: {e}")
                
            continue
            
        # Normal user goal entry
        try:
            # Save goal to session context in RAM (redacted)
            redacted_goal = redact_value(user_input)
            SESSION_CONTEXT.append({
                "timestamp": datetime.datetime.now().isoformat(),
                "goal": redacted_goal,
            })
            
            bridge.run_bridge_autonomous_loop(
                user_input,
                plan_only=False,
                debug=debug_mode
            )
        except Exception as e:
            print(f"\n[HATA] Görev çalıştırılırken hata oluştu: {e}")
            print("Prompta geri dönülüyor...\n")

def print_help_menu():
    print("""
Mevcut Komutlar:
  /help                         : Bu yardım menüsünü gösterir.
  /memory last <n>              : Son <n> görevin kaydını listeler.
  /memory search <query>        : Görev geçmişinde arama yapar.
  /providers                    : Aktif sağlayıcı (provider) ve model ayarlarını gösterir.
  /health                       : Sistem durumu, registry, memory ve local endpoint kontrollerini yapar (browser açmaz).
  /brain health                 : Tarayıcı tabanlı (Playwright) brain kontrolünü çalıştırır.
  /benchmark local              : Local model performans testlerini çalıştırır.
  /route <goal>                 : Hedefin hangi kategoriye yönlendirileceğini gösterir (çalıştırmaz).
  /plan <goal>                  : Hedefi işlem yapmadan planlar ve güvenlik kurallarını kontrol eder (hiçbir işlem uygulamaz).
  /debug [on|off]               : Teknik log/JSON gösterimini açar veya kapatır.
  /tools                        : Kullanılabilir araçları kategori kategori gösterir.
  exit / quit                   : Programdan çıkar.

Örnekler:
  * Chrome'u aç
  * Masaüstünde test klasörü oluştur
  * Bu projeyi incele ama dosya yazma
  * Windows son çökme kayıtlarını incele
  * /memory last 5
  * /tools
  * /providers
  * /plan <hedef>
""")

def print_tool_catalog():
    from tools.registry import registered_tools
    reg_tools = set(registered_tools())
    
    # Safety check: if run_command is registered, hide it.
    if "run_command" in reg_tools:
        reg_tools.discard("run_command")

    print("\n=== TOOL CATALOG ===\n")
    
    print("Local Computer:\n")
    if "open_browser" in reg_tools:
        print("* open_browser")
        print("  Açıklama: Varsayılan tarayıcıyı açar.")
        print("  Risk: low")
        print("  Örnek: Chrome'u aç\n")
    if "create_directory" in reg_tools:
        print("* create_directory")
        print("  Açıklama: Belirtilen yerde klasör oluşturur.")
        print("  Risk: low/medium")
        print("  Örnek: Masaüstünde deneme klasörü oluştur\n")
    if "open_application" in reg_tools:
        print("* open_application")
        print("  Açıklama: Allowlist içindeki güvenli uygulamaları açar.")
        print("  Risk: medium")
        print("  Örnek: Not Defteri'ni aç / Hesap makinesini aç\n")

    print("File Operations:\n")
    if "open_folder" in reg_tools:
        print("* open_folder")
        print("  Açıklama: Belirtilen klasörü Windows Explorer ile açar.")
        print("  Risk: low/medium")
        print("  Örnek: Masaüstünü aç / Belgeler klasörünü aç\n")
    if "copy_file" in reg_tools:
        print("* copy_file")
        print("  Açıklama: Dosyayı veya klasörü başka bir konuma kopyalar.")
        print("  Risk: medium")
        print("  Örnek: rapor.txt dosyasını Belgeler klasörüne kopyala\n")
    if "move_file" in reg_tools:
        print("* move_file")
        print("  Açıklama: Dosyayı veya klasörü başka bir konuma taşır.")
        print("  Risk: high")
        print("  Örnek: rapor.txt dosyasını Belgeler klasörüne taşı\n")
    if "safe_delete_file" in reg_tools:
        print("* safe_delete_file")
        print("  Açıklama: Dosyayı kalıcı silmek yerine proje içi güvenli çöp klasörüne taşır.")
        print("  Risk: high")
        print("  Örnek: rapor.txt dosyasını sil\n")
    if "search_files" in reg_tools:
        print("* search_files")
        print("  Açıklama: Belirtilen klasörde dosya adı veya uzantıya göre arama yapar.")
        print("  Risk: low")
        print("  Örnek: Masaüstünde rapor.txt dosyasını ara\n")
    if "get_file_info" in reg_tools:
        print("* get_file_info")
        print("  Açıklama: Belirtilen dosyanın boyut, oluşturulma ve değiştirilme tarihi gibi bilgilerini gösterir.")
        print("  Risk: low")
        print("  Örnek: rapor.txt dosya bilgilerini göster\n")

    print("Workspace:\n")
    workspace_tools = ["list_workspace_files", "read_file_limited", "write_file_with_diff", "validate_python_syntax_sandboxed"]
    for t in workspace_tools:
        if t in reg_tools:
            print(f"* {t}")
    print()

    print("Research:\n")
    if "web_search_public" in reg_tools:
        print("* web_search_public\n")

    print("Applications:\n")
    print("Bilinmeyen exe/path doğrudan çalıştırılmaz. Uygulama güvenli registry/shortcut/launcher üzerinden doğrulanmalıdır.\n")
    if "discover_applications" in reg_tools:
        print("* discover_applications")
        print("  Açıklama: Bilgisayarda yüklü uygulamaları tarar (salt okunur).")
        print("  Risk: low")
        print("  Örnek: discover_applications\n")
    if "launch_application_resolved" in reg_tools:
        print("* launch_application_resolved")
        print("  Açıklama: Registry tarafından doğrulanmış bir uygulamayı başlatır.")
        print("  Risk: high")
        print("  Örnek: Steam'den Counter-Strike aç\n")

    print("Diagnostics:\n")
    if "application_diagnostics" in reg_tools:
        print("* application_diagnostics")
        print("  Açıklama: Belirtilen uygulamada sorun giderme teşhisi yapar (salt okunur).")
        print("  Risk: low")
        print("  Örnek: Codex açılmıyor, takıldı kaldı, sorun ne bak\n")
    diag_tools = [
        "list_running_processes_summary",
        "list_recent_crashes",
        "read_reliability_history",
        "read_recent_event_logs",
        "get_system_info",
        "get_last_boot_reason",
        "list_driver_errors",
        "check_disk_health_readonly"
    ]
    for t in diag_tools:
        if t in reg_tools:
            print(f"* {t}")
    print()

    print("Remediation:\n")
    print("Bu araçlar uygulama kapatma, yeniden başlatma veya cache/log/config işlemleri yapabilir. Kaydedilmemiş veriler etkilenebilir. Her işlem enhanced approval ister ve plan-only modda çalışmaz.\n")
    if "close_application_process" in reg_tools:
        print("* close_application_process")
        print("  Açıklama: Belirtilen uygulamanın çalışan tüm proseslerini sonlandırır.")
        print("  Risk: high")
        print("  Örnek: Codex'i kapat\n")
    if "restart_application_resolved" in reg_tools:
        print("* restart_application_resolved")
        print("  Açıklama: Uygulamayı sonlandırıp yeniden başlatır.")
        print("  Risk: high")
        print("  Örnek: Codex'i yeniden başlat\n")
    if "archive_application_logs" in reg_tools:
        print("* archive_application_logs")
        print("  Açıklama: Log dosyalarını kopyalayarak yerel backup klasörüne arşivler.")
        print("  Risk: medium")
        print("  Örnek: Codex loglarını arşivle\n")
    if "backup_application_config" in reg_tools:
        print("* backup_application_config")
        print("  Açıklama: Konfigürasyon dosyasını yerel backup klasörüne yedekler.")
        print("  Risk: medium")
        print("  Örnek: Codex config yedekle\n")
    if "clear_safe_application_cache" in reg_tools:
        print("* clear_safe_application_cache")
        print("  Açıklama: Cache dosyalarını silmeden yedek klasörüne taşıyarak temizler.")
        print("  Risk: high")
        print("  Örnek: Codex cache temizle\n")

def print_providers_info():
    from cost_aware_provider_selector import _is_local_model_suitable
    
    print("\n=== AKTİF SAĞLAYICI VE MODEL BİLGİLERİ ===")
    print(f"LLM Provider                 : {config.LLM_PROVIDER}")
    print(f"Planner Provider             : {config.PLANNER_PROVIDER} (Model: {config.PM_MODEL})")
    print(f"Coder Provider               : {config.CODER_PROVIDER} (Model: {config.CODER_MODEL})")
    print(f"Critic Provider              : {config.CRITIC_PROVIDER} (Model: {config.CRITIC_MODEL})")
    print(f"Workspace Analysis Provider  : {config.WORKSPACE_ANALYSIS_PROVIDER}")
    print()
    
    # Fast Local Model
    fast_suitable = _is_local_model_suitable(config.LOCAL_FAST_MODEL)
    print("Fast Local Model:")
    print(f"Model: {config.LOCAL_FAST_MODEL}")
    print(f"Config flag: {config.USE_LOCAL_FAST}")
    print(f"Benchmark suitable: {fast_suitable}")
    print(f"Effective enabled: {config.USE_LOCAL_FAST and fast_suitable}")
    print()

    # Reasoner Local Model
    reasoner_suitable = _is_local_model_suitable(config.LOCAL_REASONER_MODEL)
    print("Reasoner Local Model:")
    print(f"Model: {config.LOCAL_REASONER_MODEL}")
    print(f"Config flag: {config.USE_LOCAL_CRITIC}")
    print(f"Benchmark suitable: {reasoner_suitable}")
    print(f"Effective enabled: {config.USE_LOCAL_CRITIC and reasoner_suitable}")
    print()

    # Coder Local Model
    coder_suitable = _is_local_model_suitable(config.LOCAL_CODER_MODEL)
    print("Coder Local Model:")
    print(f"Model: {config.LOCAL_CODER_MODEL}")
    print(f"Config flag: {config.USE_LOCAL_CODER}")
    print(f"Benchmark suitable: {coder_suitable}")
    print(f"Effective enabled: {config.USE_LOCAL_CODER and coder_suitable}")
    print("===========================================")

def run_safe_health_check():
    print("\n=== SYSTEM HEALTH CHECK ===")
    
    # 1. Config Check
    print("\n[1] Konfigürasyon Kontrolü:")
    print(f"  Project Root: {config.PROJECT_ROOT}")
    print(f"  LLM Provider: {config.LLM_PROVIDER}")
    print(f"  Workspace Analysis Provider: {config.WORKSPACE_ANALYSIS_PROVIDER}")
    if config.GROQ_API_KEY:
        print("  Groq API Key: Ayarlanmış (Gizli)")
    else:
        print("  Groq API Key: AYARLANMAMIŞ (Uyarı)")
        
    # 2. Registry Check
    print("\n[2] Tool Registry Kontrolü:")
    from tools.registry import registered_tools
    tools = registered_tools()
    print(f"  Kayıtlı Tool Sayısı: {len(tools)}")
    print(f"  Kayıtlı Araçlar: {', '.join(tools)}")
    
    # 3. Memory File Check
    print("\n[3] Memory Dosyası Kontrolü:")
    memory_file = os.path.join(config.PROJECT_ROOT, "logs", "task_memory.jsonl")
    if os.path.exists(memory_file):
        size = os.path.getsize(memory_file)
        try:
            with open(memory_file, "r", encoding="utf-8") as f:
                lines = sum(1 for _ in f)
        except Exception:
            lines = 0
        print(f"  Dosya Yolu: {memory_file}")
        print(f"  Dosya Boyutu: {size} bytes")
        print(f"  Kaydedilmiş Görev Sayısı: {lines}")
    else:
        print("  Memory dosyası henüz oluşturulmamış (logs/task_memory.jsonl).")
        
    # 4. Public Web Tool & İnternet Bağlantısı Kontrolü
    print("\n[4] Public Web Tool & İnternet Bağlantısı Kontrolü:")
    if "web_search_public" in tools:
        print("  web_search_public toolu kayıtlı.")
    else:
        print("  HATA: web_search_public toolu kayıtlı değil!")
    
    from urllib import request
    try:
        with request.urlopen("https://www.google.com", timeout=3) as conn:
            status = conn.status
        print(f"  İnternet Bağlantısı: OK (Google status={status})")
    except Exception as exc:
        print(f"  İnternet Bağlantısı Hata: {exc}")
        
    # 5. Local Model Endpoint Kontrolü
    print("\n[5] Local Model Endpoint Kontrolü:")
    from local_model_provider import _check_ollama, _check_lmstudio
    
    print(f"  Varsayılan Local Model Sağlayıcı: {config.LOCAL_MODEL_PROVIDER}")
    
    ollama_res = _check_ollama()
    ollama_status = "AÇIK" if ollama_res.get("ok") else "KAPALI"
    print(f"  - Ollama Endpoint: {ollama_status} ({config.OLLAMA_BASE_URL})")
    if ollama_res.get("models"):
        print(f"    Modeller: {', '.join(ollama_res['models'])}")
    elif ollama_res.get("error"):
        print(f"    Hata: {ollama_res['error']}")
        
    lm_res = _check_lmstudio()
    lm_status = "AÇIK" if lm_res.get("ok") else "KAPALI"
    print(f"  - LM Studio Endpoint: {lm_status} ({config.LMSTUDIO_BASE_URL})")
    if lm_res.get("models"):
        print(f"    Modeller: {', '.join(lm_res['models'])}")
    elif lm_res.get("error"):
        print(f"    Hata: {lm_res['error']}")
    print("\n===========================")

if __name__ == "__main__":
    run_interactive_shell()
