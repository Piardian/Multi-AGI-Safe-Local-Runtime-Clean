# -*- coding: utf-8 -*-
"""
orchestrator.py — Ana Orkestrator
====================================
Otonom Quant Laboratuvarinin merkez beyni.
PM -> Coder -> Test -> Critic dongusunu yonetir.
Tum ajan iletisimi bu modul uzerinden gecer.

Kullanim:
    python orchestrator.py                  # Normal calistirma
    python orchestrator.py --dry-run        # Kuru calistirma (dosya yazmaz)
    python orchestrator.py --test           # Baglanti testi
    python orchestrator.py --iterations 10  # Maks iterasyon sayisi
"""

import os
import sys
import json
import shutil
import argparse
import logging
import time
import tempfile
from datetime import datetime
from pathlib import Path

# ── Windows konsol encoding duzeltmesi ──
# cp1254 (Turkce Windows) emoji/unicode desteklemiyor
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass  # Eski Python versiyonlarinda reconfigure olmayabilir

import config
import security
import agents

logger = logging.getLogger("orchestrator")


# ==========================================================
# BANNER
# ==========================================================

BANNER = """
+==============================================================+
|                                                              |
|    QQQQQ   U   U   A   N   N TTTTT                          |
|   Q     Q  U   U  A A  NN  N   T                            |
|   Q     Q  U   U AAAAA N N N   T                            |
|   Q   Q Q  U   U A   A N  NN   T                            |
|    QQQQQ Q  UUU  A   A N   N   T                            |
|                                                              |
|        L       A   BBBB                                      |
|        L      A A  B   B                                     |
|        L     AAAAA BBBB                                      |
|        L     A   A B   B                                     |
|        LLLLL A   A BBBB                                      |
|                                                              |
|     Hipotez Odakli Otonom Quant Laboratuvari -- V1           |
|     ------------------------------------------------         |
|     "Rastgele filtre eklemek yasaktir."                      |
|                                                              |
+==============================================================+
"""


# ==========================================================
# ORKESTRATOR SINIFI
# ==========================================================

class Orchestrator:
    """
    Otonom Quant Laboratuvari Ana Orkestratoru.
    PM -> Coder -> Test -> Critic dongusunu yonetir.
    """

    def __init__(self, dry_run: bool = False, max_iterations: int = None):
        """
        Args:
            dry_run: True ise dosya yazmaz, API cagrilarini loglar
            max_iterations: Maksimum iterasyon sayisi (None = config'den)
        """
        self.dry_run = dry_run or config.DRY_RUN
        self.max_iterations = max_iterations or config.MAX_ITERATIONS
        self.iteration = 0
        self.context_history = []  # Onceki iterasyonlarin ozeti
        self.strategy_file = config.STRATEGY_FILE
        self.start_time = None

        # Durum
        self._running = False

        self._load_context_history()

    # -------------------------------------------------
    # ANA DONGU
    # -------------------------------------------------
    def run(self):
        """
        Ana orkestrator dongusu.
        PM -> Coder -> Test -> Critic zincirini tekrarlar.
        """
        if not config.EXPERIMENTAL_QUANT_WORKER:
            raise security.SecurityError(
                "Legacy Quant worker is isolated from the official runtime. "
                "Set EXPERIMENTAL_QUANT_WORKER=true only for lab dry-runs."
            )
        if not self.dry_run:
            raise security.SecurityError(
                "Legacy Quant worker may only run in dry-run mode. "
                "It cannot write or execute model-generated code outside TaskRuntime."
            )

        self.start_time = datetime.now()
        self._running = True

        print(BANNER)
        logger.info("[BASLA] Orkestrator baslatiliyor...")
        logger.info(f"   Proje koku       : {config.PROJECT_ROOT}")
        logger.info(f"   Strateji dosyasi : {self.strategy_file}")
        logger.info(f"   PM modu          : {config.PM_MODE}")
        logger.info(f"   Dry-run          : {self.dry_run}")
        logger.info(f"   Maks iterasyon   : {self.max_iterations}")

        if self.dry_run:
            logger.info("[UYARI] DRY-RUN MODU -- Dosya yazma ve subprocess devre disi")

        # Baslangic context'i
        initial_context = self._build_initial_context()

        try:
            while self._running and self.iteration < self.max_iterations:
                self.iteration += 1
                separator = "=" * 60
                print(f"\n{separator}")
                logger.info(
                    f"=== ITERASYON {self.iteration}/{self.max_iterations} ==="
                )
                print(separator)

                iteration_data = {
                    "iteration": self.iteration,
                    "timestamp": datetime.now().isoformat(),
                    "dry_run": self.dry_run,
                }

                try:
                    # -- ADIM 1: PM'den hipotez al --
                    context = self._build_context(initial_context)
                    hypothesis = self._step_pm(context)
                    iteration_data["hypothesis"] = hypothesis

                    if not hypothesis:
                        logger.error("[HATA] PM ajani bos yanit dondu. Atlaniyor...")
                        continue

                    # -- ADIM 2: Mevcut kodu oku --
                    current_code = self._read_current_strategy()
                    iteration_data["previous_code_length"] = len(current_code)

                    # -- ADIM 3: Coder'a hipotezi gonder --
                    new_code = self._step_coder(hypothesis, current_code)
                    iteration_data["new_code_length"] = len(new_code)

                    if not new_code:
                        logger.error("[HATA] Coder ajani bos yanit dondu. Atlaniyor...")
                        continue

                    # -- ADIM 4: Yedekle ve yaz --
                    self._backup_strategy()
                    self._apply_code(new_code)

                    # -- ADIM 5: Backtest calistir --
                    stdout, stderr, returncode = self._step_test()
                    iteration_data["test_stdout"] = stdout[:2000]
                    iteration_data["test_stderr"] = stderr[:2000]
                    iteration_data["test_returncode"] = returncode

                    # -- ADIM 6: Critic'e gonder --
                    critique = self._step_critic(stdout, stderr, hypothesis)
                    iteration_data["critique"] = critique

                    if returncode != 0 and config.STOP_ON_TEST_FAILURE:
                        logger.error(
                            "[DUR] Test hata verdi; otomatik dongu durduruldu "
                            "(STOP_ON_TEST_FAILURE=true)."
                        )
                        self._running = False

                    # -- ADIM 7: Context'i guncelle --
                    self._update_context(hypothesis, critique, stdout, stderr)
                    iteration_data["status"] = "COMPLETED"

                except security.SecurityError as e:
                    logger.error(f"[GUVENLIK] GUVENLIK HATASI: {e}")
                    iteration_data["status"] = "SECURITY_ERROR"
                    iteration_data["error"] = str(e)

                except security.ApprovalDeniedError as e:
                    logger.warning(f"[RED] ONAY REDDEDILDI: {e}")
                    iteration_data["status"] = "APPROVAL_DENIED"
                    iteration_data["error"] = str(e)

                except KeyboardInterrupt:
                    logger.info("[DUR] Kullanici tarafindan durduruldu (Ctrl+C)")
                    iteration_data["status"] = "USER_INTERRUPTED"
                    self._running = False

                except Exception as e:
                    logger.error(f"[HATA] Iterasyon hatasi: {e}", exc_info=True)
                    iteration_data["status"] = "ERROR"
                    iteration_data["error"] = str(e)

                finally:
                    # Her iterasyonu logla
                    self._save_iteration_log(iteration_data)

                    # Rate limit korumasi
                    if self._running:
                        logger.info(
                            f"[BEKLEME] Sonraki iterasyon icin "
                            f"{config.ITERATION_COOLDOWN}s bekleniyor..."
                        )
                        time.sleep(config.ITERATION_COOLDOWN)

        except KeyboardInterrupt:
            logger.info("\n[DUR] Orkestrator kullanici tarafindan durduruldu.")

        finally:
            self._print_summary()

    # -------------------------------------------------
    # ADIM FONKSIYONLARI
    # -------------------------------------------------

    def _step_pm(self, context: str) -> str:
        """ADIM 1: PM ajanindan hipotez al."""
        logger.info("[ADIM 1/6] PM Ajani -- Hipotez uretimi")

        if self.dry_run:
            logger.info("  [DRY-RUN] PM cagrisi simule ediliyor")
            return (
                "1. HIPOTEZ: [DRY-RUN] Test hipotezi\n"
                "2. MANTIK: Kuru calistirma modu\n"
                "3. UYGULAMA: pass\n"
                "4. RED KRITERI: N/A"
            )

        try:
            hypothesis = agents.call_pm_agent(context)
            logger.info(f"  [OK] Hipotez alindi ({len(hypothesis)} karakter)")
            return hypothesis
        except Exception as e:
            logger.error(f"  [HATA] PM hatasi: {e}")
            raise

    def _step_coder(self, hypothesis: str, current_code: str) -> str:
        """ADIM 3: Coder ajanindan kod al."""
        logger.info("[ADIM 3/6] Coder Ajani -- Kod uretimi")

        if self.dry_run:
            logger.info("  [DRY-RUN] Coder cagrisi simule ediliyor")
            return current_code  # Mevcut kodu degistirme

        try:
            new_code = agents.call_coder_agent(hypothesis, current_code)
            logger.info(f"  [OK] Kod alindi ({len(new_code)} karakter)")
            return new_code
        except Exception as e:
            logger.error(f"  [HATA] Coder hatasi: {e}")
            raise

    def _step_test(self) -> tuple:
        """ADIM 5: Backtest calistir."""
        logger.info("[ADIM 5/6] Test -- Subprocess calistirma")

        if self.dry_run:
            logger.info("  [DRY-RUN] Subprocess simule ediliyor")
            return '{"status": "DRY_RUN", "total_trades": 0}', "", 0

        try:
            # Dogru Python yolunu bul
            python_exe = sys.executable
            cmd = f'"{python_exe}" "{self.strategy_file}"'
            stdout, stderr, returncode = security.safe_run_subprocess(
                cmd=cmd,
                cwd=config.PROJECT_ROOT,
            )

            if returncode == 0:
                logger.info("  [OK] Test basariyla tamamlandi")
            else:
                logger.warning(f"  [UYARI] Test hata ile bitti (code={returncode})")

            if stdout:
                logger.debug(f"  stdout: {stdout[:500]}")
            if stderr:
                logger.debug(f"  stderr: {stderr[:500]}")

            return stdout, stderr, returncode

        except (security.SecurityError, security.ApprovalDeniedError):
            raise
        except Exception as e:
            logger.error(f"  [HATA] Test hatasi: {e}")
            return "", str(e), -1

    def _step_critic(self, stdout: str, stderr: str, hypothesis: str) -> str:
        """ADIM 6: Critic ajanindan analiz al."""
        logger.info("[ADIM 6/6] Critic Ajani -- Analiz")

        if self.dry_run:
            logger.info("  [DRY-RUN] Critic cagrisi simule ediliyor")
            return (
                "DURUM: DRY_RUN\n"
                "KOD_DURUMU: SIMULE\n"
                "ANALIZ: Kuru calistirma modu -- gercek analiz yapilmadi\n"
                "SONRAKI_ADIM: Gercek calistirma icin --dry-run bayragini kaldirin"
            )

        try:
            critique = agents.call_critic_agent(
                test_results=stdout,
                hypothesis=hypothesis,
                stderr=stderr,
            )
            logger.info(f"  [OK] Analiz alindi ({len(critique)} karakter)")
            return critique
        except Exception as e:
            logger.error(f"  [HATA] Critic hatasi: {e}")
            raise

    # -------------------------------------------------
    # DOSYA ISLEMLERI
    # -------------------------------------------------

    def _read_current_strategy(self) -> str:
        """Mevcut strateji dosyasini oku."""
        try:
            return security.safe_read_file(self.strategy_file)
        except FileNotFoundError:
            logger.warning(
                f"Strateji dosyasi bulunamadi: {self.strategy_file}. "
                "Bos dosya olusturuluyor..."
            )
            security.safe_write_file(
                self.strategy_file,
                '# Bos strateji dosyasi -- ajanlar tarafindan doldurulacak\n'
            )
            return ""

    def _backup_strategy(self):
        """
        Mevcut strateji dosyasinin yedegini alir.
        Yedek format: backups/strategy_v{iteration}_{timestamp}.py
        """
        if self.dry_run:
            return

        if not os.path.exists(self.strategy_file):
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"strategy_v{self.iteration}_{timestamp}.py"
        backup_path = os.path.join(config.BACKUP_DIR, backup_name)

        try:
            validated_src = security.validate_path(self.strategy_file)
            validated_dst = security.validate_path(backup_path)
            shutil.copy2(validated_src, validated_dst)
            logger.info(f"[YEDEK] Yedek olusturuldu: {backup_name}")
        except Exception as e:
            logger.error(f"Yedekleme hatasi: {e}")

    def _apply_code(self, code: str):
        """
        Coder'dan gelen kodu strateji dosyasina yazar.

        Args:
            code: Yazilacak Python kodu
        """
        if self.dry_run:
            logger.info("  [DRY-RUN] Kod yazma simule ediliyor")
            return

        code = self._extract_python_code(code)
        self._validate_python_code(code)

        logger.info(f"[YAZ] Kod yaziliyor: {os.path.basename(self.strategy_file)}")

        try:
            security.safe_write_file(self.strategy_file, code)
        except security.SecurityError as e:
            logger.error(f"[GUVENLIK] Kod yazma guvenlik hatasi: {e}")
            raise

    def _extract_python_code(self, code: str) -> str:
        """Markdown veya aciklama icinden yazilabilir Python kodunu ayiklar."""
        if "```" not in code:
            return code.strip()

        blocks = []
        parts = code.split("```")
        for index in range(1, len(parts), 2):
            block = parts[index].strip()
            if block.lower().startswith("python"):
                block = block.splitlines()[1:]
                blocks.append("\n".join(block).strip())
            elif block.startswith("#") or "def " in block or "import " in block:
                blocks.append(block)

        if not blocks:
            raise ValueError("Coder yanitinda Python kod blogu bulunamadi.")

        return max(blocks, key=len).strip()

    def _validate_python_code(self, code: str):
        """Strateji yazilmadan once Python olarak derlenebilir oldugunu kontrol eder."""
        if not code:
            raise ValueError("Coder bos kod dondurdu.")

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".py",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        try:
            cmd = f'"{sys.executable}" -m py_compile "{tmp_path}"'
            stdout, stderr, returncode = security.safe_run_subprocess(
                cmd=cmd,
                cwd=config.PROJECT_ROOT,
            )
            if returncode != 0:
                raise ValueError(
                    "Coder gecersiz Python kodu dondurdu; strategy.py korunuyor. "
                    f"{(stderr or stdout)[:500]}"
                )
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # -------------------------------------------------
    # CONTEXT YONETIMI
    # -------------------------------------------------

    def _build_initial_context(self) -> str:
        """Baslangic context'ini olusturur."""
        current_code = self._read_current_strategy()

        return (
            "Bu ilk iterasyon. Henuz test edilmis bir hipotez yok.\n\n"
            f"=== MEVCUT STRATEJI KODU ===\n{current_code}\n\n"
            "=== GOREV ===\n"
            "Mevcut iskelet kodu analiz et ve ilk hipotezini kur. "
            "Likidite alimi, HTF POI tepkileri veya yapisal kirilmalar (FVG/Displacement) "
            "uzerinden bir piyasa davranisi iddiasi olustur. "
            "Ardindan bu hipotezi kodlamak icin Coder'a net talimatlar ver."
        )

    def _build_context(self, initial_context: str) -> str:
        """Her iterasyon icin guncel context olusturur."""
        if not self.context_history:
            return initial_context

        # Son 3 iterasyonun ozetini dahil et (token tasarrufu)
        recent = self.context_history[-3:]
        history_text = ""

        for entry in recent:
            history_text += (
                f"\n-- Iterasyon {entry['iteration']} --\n"
                f"Hipotez: {entry.get('hypothesis_summary', 'N/A')}\n"
                f"Test sonucu: {entry.get('test_summary', 'N/A')}\n"
                f"Critic: {entry.get('critique_summary', 'N/A')}\n"
            )

        current_code = self._read_current_strategy()

        return (
            f"Iterasyon: {self.iteration}/{self.max_iterations}\n\n"
            f"=== ONCEKI ITERASYONLAR ===\n{history_text}\n\n"
            f"=== MEVCUT STRATEJI KODU ===\n{current_code}\n\n"
            f"=== GOREV ===\n"
            f"Onceki iterasyonlarin sonuclarini dikkate alarak "
            f"yeni bir hipotez kur veya mevcut hipotezi iyilestir. "
            f"Critic'in geri bildirimlerini ihmal etme."
        )

    def _update_context(
        self,
        hypothesis: str,
        critique: str,
        stdout: str,
        stderr: str,
    ):
        """Context gecmisini gunceller."""
        entry = {
            "iteration": self.iteration,
            "hypothesis_summary": hypothesis[:500] if hypothesis else "N/A",
            "test_summary": (
                stdout[:300] if stdout else
                (f"HATA: {stderr[:300]}" if stderr else "Cikti yok")
            ),
            "critique_summary": critique[:500] if critique else "N/A",
        }
        self.context_history.append(entry)

    def _load_context_history(self):
        """Onceki calistirmalardaki iterasyon ozetlerini log dosyasindan yukler."""
        log_file = os.path.join(config.LOG_DIR, "iterations.jsonl")
        if not os.path.exists(log_file):
            return

        loaded_history = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "Gecmis log satiri okunamadi, atlaniyor: %s",
                            line_number,
                        )
                        continue

                    iteration = int(data.get("iteration") or 0)

                    loaded_history.append(
                        {
                            "iteration": iteration,
                            "hypothesis_summary": (data.get("hypothesis") or "N/A")[:500],
                            "test_summary": (
                                (data.get("test_stdout") or "")[:300]
                                or (
                                    f"HATA: {(data.get('test_stderr') or '')[:300]}"
                                    if data.get("test_stderr")
                                    else "Cikti yok"
                                )
                            ),
                            "critique_summary": (data.get("critique") or "N/A")[:500],
                        }
                    )
        except Exception as e:
            logger.error("Gecmis iterasyon loglari yuklenemedi: %s", e)
            return

        self.context_history = loaded_history

        if loaded_history:
            logger.info(
                "[GECMIS] %s iterasyon sohbet gecmisi geri yuklendi.",
                len(loaded_history),
            )

    # -------------------------------------------------
    # LOGLAMA
    # -------------------------------------------------

    def _save_iteration_log(self, data: dict):
        """
        Her iterasyonu JSON Lines formatinda loglar.

        Args:
            data: Iterasyon verileri
        """
        log_file = os.path.join(config.LOG_DIR, "iterations.jsonl")

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.error(f"Iterasyon logu yazilamadi: {e}")

    def _print_summary(self):
        """Calistirma ozetini yazdirir."""
        elapsed = datetime.now() - self.start_time if self.start_time else None

        print(f"\n{'=' * 60}")
        print("[OZET] ORKESTRATOR OZETI")
        print(f"{'=' * 60}")
        print(f"  Toplam iterasyon  : {self.iteration}")
        print(f"  Calisma suresi    : {elapsed}")
        print(f"  Dry-run           : {self.dry_run}")
        print(f"  Yedek dosyalar    : {config.BACKUP_DIR}")
        print(f"  Log dosyalari     : {config.LOG_DIR}")
        print(f"{'=' * 60}\n")


# ==========================================================
# BAGLANTI TESTI
# ==========================================================

def run_connection_test():
    """Tum bilesenlerin baglantisini test eder."""
    print(BANNER)
    print("[TEST] Baglanti Testi Baslatiliyor...\n")

    results = {}

    # 1. Konfigrasyon
    print("[1/4] Konfigurasyon kontrolu...")
    results["config"] = config.validate_config()

    # 2. Guvenlik modulu
    print("\n[2/4] Guvenlik modulu testi...")
    try:
        # Izin verilen yol
        security.validate_path(config.PROJECT_ROOT)
        print("  [OK] Izin verilen yol dogrulandi")

        # Yasak yol
        try:
            security.validate_path("C:/Windows/System32")
            print("  [HATA] Yasak yol kabul edildi -- GUVENLIK ACIGI!")
            results["security"] = False
        except security.SecurityError:
            print("  [OK] Yasak yol dogru sekilde reddedildi")
            results["security"] = True

    except Exception as e:
        print(f"  [HATA] Guvenlik testi hatasi: {e}")
        results["security"] = False

    # 3. Groq API
    print("\n[3/4] Groq API baglanti testi...")
    if config.GROQ_API_KEY:
        results["groq"] = agents.test_groq_connection()
    else:
        print("  [UYARI] GROQ_API_KEY ayarlanmamis -- test atlaniyor")
        results["groq"] = False

    # 4. Strateji dosyasi
    print("\n[4/4] Strateji dosyasi kontrolu...")
    if os.path.exists(config.STRATEGY_FILE):
        print(f"  [OK] Strateji dosyasi mevcut: {config.STRATEGY_FILE}")
        results["strategy"] = True
    else:
        print(f"  [UYARI] Strateji dosyasi bulunamadi: {config.STRATEGY_FILE}")
        print("  [BILGI] Ilk iterasyonda otomatik olusturulacak")
        results["strategy"] = True

    # Ozet
    print(f"\n{'-' * 40}")
    all_ok = all(results.values())
    if all_ok:
        print("[OK] Tum testler basarili! Sistem calismaya hazir.")
        print("   Calistirmak icin: python orchestrator.py")
    else:
        print("[UYARI] Bazi testler basarisiz:")
        for k, v in results.items():
            status = "[OK]" if v else "[HATA]"
            print(f"   {status} {k}")
        print("\n   Sorunlari duzelttikten sonra tekrar deneyin.")

    return all_ok


# ==========================================================
# CLI GIRIS NOKTASI
# ==========================================================

def main():
    """Komut satiri giris noktasi."""
    parser = argparse.ArgumentParser(
        description="Otonom Quant Laboratuvari -- Orkestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ornekler:\n"
            "  python orchestrator.py                  # Normal calistirma\n"
            "  python orchestrator.py --dry-run        # Kuru calistirma\n"
            "  python orchestrator.py --test           # Baglanti testi\n"
            "  python orchestrator.py --iterations 5   # 5 iterasyon calistir\n"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Kuru calistirma -- API cagrilari simule edilir, dosya yazilmaz",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Baglanti testi -- tum bilesenleri kontrol eder",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help=f"Maksimum iterasyon sayisi (varsayilan: {config.MAX_ITERATIONS})",
    )

    args = parser.parse_args()

    # Loglamayi baslat
    config.setup_logging()

    # Baglanti testi modu
    if args.test:
        success = run_connection_test()
        sys.exit(0 if success else 1)

    # Konfigurasyon dogrulama (dry_run modunda API key zorunlu degil)
    if not config.validate_config(dry_run=args.dry_run):
        logger.error("[HATA] Konfigurasyon hatalari var. Lutfen duzeltin.")
        sys.exit(1)

    # Orkestratoru baslat
    orchestrator = Orchestrator(
        dry_run=args.dry_run,
        max_iterations=args.iterations,
    )

    try:
        orchestrator.run()
    except Exception as e:
        logger.critical(f"[KRITIK] Kritik hata: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
