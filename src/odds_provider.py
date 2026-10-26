"""
実際の発走前オッズ取得元プロバイダーおよび接続診断・再試行モジュール (Pre-Race Odds Providers)

【基本原則・制約】
1. 実際の発走前オッズ取得元（JV-Link / 許諾フィード / リアルタイムWeb）の接続状態を厳格診断
2. HTTP 200だけで接続成功扱いとすることを禁止。利用許諾・実データ・時刻情報が確認できなければ「未接続(DISCONNECTED)」
3. 未接続・未設定の場合は架空データで成功扱いせず欠損扱い（フェイルクローズ）
4. 取得処理における指数バックオフ付き再試行（Retry）を実装
5. 発走時刻以降の確定オッズは完全遮断
"""
from __future__ import annotations
import datetime
import importlib
import json
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
import requests
from bs4 import BeautifulSoup

repo_root = Path(__file__).resolve().parent.parent


class BaseOddsProvider(ABC):
    """オッズ取得プロバイダー基底クラス"""
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def check_connection(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        """接続状態・認可状態の診断（ネットワーク疎通・資格情報・実データ・時刻情報の有無）"""
        pass

    @abstractmethod
    def fetch_prerace_odds(
        self,
        race_id: str,
        scheduled_race_time: str,
        timing_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        """発走前オッズの取得（取得不可の場合は None を返却、架空データ生成は禁止）"""
        pass


class JVLinkOddsProvider(BaseOddsProvider):
    """JRA-VAN JV-Link COM SDK プロバイダー（公式認可フィード）"""
    def __init__(self):
        super().__init__("JV-Link")

    def check_connection(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            win32com = importlib.import_module("win32com.client")
            jv = win32com.Dispatch("JVDTLab.JVLink")
            return {
                "provider": self.name,
                "connected": True,
                "status": "READY",
                "note": "JVDTLab.JVLink COM component is initialized."
            }
        except ImportError:
            return {
                "provider": self.name,
                "connected": False,
                "status": "DISCONNECTED",
                "reason": "win32com library is not installed in current Python environment."
            }
        except Exception as e:
            return {
                "provider": self.name,
                "connected": False,
                "status": "DISCONNECTED",
                "reason": f"JVLink COM component unavailable: {e}"
            }

    def fetch_prerace_odds(
        self,
        race_id: str,
        scheduled_race_time: str,
        timing_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        diag = self.check_connection(race_id)
        if not diag["connected"]:
            return None
        return None


class LicensedFeedOddsProvider(BaseOddsProvider):
    """許諾済み事前配信ローカルフィードプロバイダー（Phase 12 仕様）"""
    def __init__(self, feed_dir: Optional[Path] = None):
        super().__init__("LicensedFeed")
        self.feed_dir = feed_dir or (repo_root / "data" / "feeds" / "prerace_odds")

    def check_connection(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        feed_contract_path = repo_root / "config" / "phase12_feed_contract.json"
        has_dir = self.feed_dir.exists()
        has_contract = feed_contract_path.exists()

        if has_dir and has_contract:
            return {
                "provider": self.name,
                "connected": True,
                "status": "READY",
                "feed_dir": str(self.feed_dir)
            }
        return {
            "provider": self.name,
            "connected": False,
            "status": "DISCONNECTED",
            "reason": f"Licensed feed directory ({self.feed_dir}) or contract ({feed_contract_path}) not configured."
        }

    def fetch_prerace_odds(
        self,
        race_id: str,
        scheduled_race_time: str,
        timing_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.feed_dir.exists():
            return None
        target_file = self.feed_dir / f"{race_id}_{timing_tag}.json"
        if not target_file.exists():
            return None
        try:
            data = json.loads(target_file.read_text(encoding="utf-8"))
            return data.get("odds") if isinstance(data, dict) else data
        except Exception:
            return None


class NetkeibaLiveOddsProvider(BaseOddsProvider):
    """
    リアルタイムWebオッズプロバイダー
    【厳格ルール】
    - 単なる HTTP 200 応答のみで接続成功扱いとすることを禁止
    - 1. 利用許諾 (ライセンス契約)
    - 2. 実データ (発走前オッズテーブルの存在)
    - 3. 公式発表時刻情報 (observed_at)
    上記3点がすべて確認できなければ「未接続(DISCONNECTED)」としてフェイルクローズ処理する。
    """
    def __init__(self, timeout_sec: float = 6.0, license_confirmed: bool = False):
        super().__init__("NetkeibaLive")
        self.timeout_sec = timeout_sec
        self.license_confirmed = license_confirmed
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def check_connection(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        # 1. 利用許諾チェック (Terms of Service / Licensing Check)
        contract_file = repo_root / "config" / "netkeiba_license.json"
        has_license = self.license_confirmed or contract_file.exists()
        if not has_license:
            return {
                "provider": self.name,
                "connected": False,
                "status": "DISCONNECTED",
                "reason": "利用許諾未確認: Netkeiba利用規約に基づくAPI許諾契約が未設定です (config/netkeiba_license.json 不在)。HTTP 200疎通のみでの接続認可は禁止されています。"
            }

        # 2. 実データおよび時刻情報チェック
        if not race_id:
            return {
                "provider": self.name,
                "connected": False,
                "status": "DISCONNECTED",
                "reason": "実データ・時刻情報未確認: 対象レース未指定のため、発走前オッズ表および公式発表時刻が検証されていません。"
            }

        url = f"https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b1"
        try:
            resp = requests.get(url, headers=self.headers, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return {
                    "provider": self.name,
                    "connected": False,
                    "status": "DISCONNECTED",
                    "reason": f"HTTPステータス異常: {resp.status_code}"
                }

            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", id="odds_tan_block") or soup.find("table", class_=re.compile("Odds_Table"))
            if not table:
                return {
                    "provider": self.name,
                    "connected": False,
                    "status": "DISCONNECTED",
                    "reason": "実データ欠落: 該当レースの発走前単勝オッズテーブルが見つかりません（非開催日または未発表）。"
                }

            # 3. 公式発表時刻情報の確認
            time_el = soup.find(class_=re.compile(r"Update|Time|Date|odds_update")) or soup.find(string=re.compile(r"\d{1,2}:\d{2}更新"))
            if not time_el:
                return {
                    "provider": self.name,
                    "connected": False,
                    "status": "DISCONNECTED",
                    "reason": "公式発表時刻情報(observed_at)欠落: 提供元の発表時刻を特定・検証できません。"
                }

            return {
                "provider": self.name,
                "connected": True,
                "status": "READY",
                "note": "利用許諾・実オッズデータ・公式発表時刻のすべてを確認完了。"
            }

        except Exception as e:
            return {
                "provider": self.name,
                "connected": False,
                "status": "DISCONNECTED",
                "reason": f"疎通・解析失敗: {e}"
            }

    def fetch_prerace_odds(
        self,
        race_id: str,
        scheduled_race_time: str,
        timing_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        diag = self.check_connection(race_id)
        if not diag["connected"]:
            return None

        # 接続が認可されている場合のみ実データ解析を実施
        url = f"https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b1"
        # Never fabricate a publication timestamp from local fetch time.
        # This provider has no verified publication-time parser; fail closed.
        return None

        # Unreachable until a licensed, verified timestamp parser is implemented.
        now_iso = datetime.datetime.now().isoformat()
        try:
            resp = requests.get(url, headers=self.headers, timeout=self.timeout_sec)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", id="odds_tan_block") or soup.find("table", class_=re.compile("Odds_Table"))
            if not table:
                return None

            records = []
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 3:
                    continue
                hn_txt = tds[1].get_text(strip=True) if len(tds) > 1 else ""
                if not hn_txt.isdigit():
                    continue
                hn = int(hn_txt)

                odds_txt = tds[2].get_text(strip=True) if len(tds) > 2 else ""
                try:
                    odds_val = float(odds_txt)
                    if odds_val < 1.0:
                        continue
                except ValueError:
                    continue

                pop_txt = tds[3].get_text(strip=True) if len(tds) > 3 and tds[3].get_text(strip=True).isdigit() else "0"
                pop = int(pop_txt) if pop_txt.isdigit() else 0

                records.append({
                    "race_id": str(race_id),
                    "horse_number": hn,
                    "odds_value": odds_val,
                    "popularity": pop,
                    "observed_at": now_iso,
                    "fetched_at": now_iso,
                    "source": "NetkeibaLive",
                    "data_status": timing_tag,
                    "timing_tag": timing_tag
                })
            return records if records else None
        except Exception:
            return None


class FileSnapshotOddsProvider(BaseOddsProvider):
    """ローカル保存スナップショットプロバイダー（監査・手動取り込み用）"""
    def __init__(self, snapshot_dir: Optional[Path] = None):
        super().__init__("FileSnapshot")
        self.snapshot_dir = snapshot_dir or (repo_root / "data" / "snapshots")

    def check_connection(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        has_dir = self.snapshot_dir.exists()
        if not has_dir:
            return {
                "provider": self.name,
                "connected": False,
                "status": "DISCONNECTED",
                "snapshot_dir": str(self.snapshot_dir),
                "reason": f"Directory {self.snapshot_dir} does not exist."
            }
        if race_id:
            files = list(self.snapshot_dir.glob(f"*{race_id}*.json"))
            if not files:
                return {
                    "provider": self.name,
                    "connected": False,
                    "status": "DISCONNECTED",
                    "reason": f"No snapshot files found for race_id {race_id} in {self.snapshot_dir}."
                }
        return {
            "provider": self.name,
            "connected": True,
            "status": "READY",
            "snapshot_dir": str(self.snapshot_dir)
        }

    def fetch_prerace_odds(
        self,
        race_id: str,
        scheduled_race_time: str,
        timing_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.snapshot_dir.exists():
            return None
        candidate = self.snapshot_dir / f"odds_{race_id}_{timing_tag}.json"
        if not candidate.exists():
            return None
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return data.get("odds") if isinstance(data, dict) and "odds" in data else (data if isinstance(data, list) else None)
        except Exception:
            return None


class OddsProviderManager:
    """発走前オッズプロバイダー統合マネージャー（診断・再試行・欠損管理）"""
    def __init__(self, providers: Optional[List[BaseOddsProvider]] = None):
        self.providers = providers or [
            JVLinkOddsProvider(),
            LicensedFeedOddsProvider(),
            NetkeibaLiveOddsProvider(),
            FileSnapshotOddsProvider(),
        ]

    def diagnose_all_providers(self, race_id: Optional[str] = None) -> Dict[str, Any]:
        """全プロバイダーの接続状態を診断して包括レポートを返す"""
        diagnostics = []
        connected_count = 0
        for p in self.providers:
            diag = p.check_connection(race_id=race_id)
            diagnostics.append(diag)
            if diag.get("connected"):
                connected_count += 1

        all_disconnected = (connected_count == 0)
        return {
            "timestamp": datetime.datetime.now().isoformat(),
            "total_providers": len(self.providers),
            "connected_providers": connected_count,
            "all_sources_disconnected": all_disconnected,
            "status": "ALL_SOURCES_DISCONNECTED" if all_disconnected else "SOME_SOURCES_CONNECTED",
            "details": diagnostics,
            "policy": "HTTP 200のみでの認可は禁止。利用許諾・実オッズデータ・公式発表時刻が未確認の場合は未接続(DISCONNECTED)とし、架空データ代用を排除して欠損(MISSING)として処理します。"
        }

    def fetch_prerace_odds_with_retry(
        self,
        race_id: str,
        scheduled_race_time: str,
        timing_tag: str,
        *,
        max_retries: int = 3,
        backoff_sec: float = 1.0,
        explicit_records: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
        """
        再試行ロジック付きで発走前オッズを取得する。
        - 明示的なオッズレコード（ファイル等）が指定されていれば最優先
        - 登録プロバイダーを順次探索し、失敗時は指数バックオフで再試行
        - すべて失敗または未接続の場合は None を返却（架空データ生成は絶対に禁止）
        """
        audit_trail: Dict[str, Any] = {
            "race_id": str(race_id),
            "timing_tag": timing_tag,
            "max_retries": max_retries,
            "attempts": 0,
            "success_source": None,
            "retry_errors": [],
            "outcome": "missing"
        }

        # 1. 明示的レコードがある場合
        if explicit_records:
            audit_trail["attempts"] = 1
            audit_trail["success_source"] = "explicit_input"
            audit_trail["outcome"] = "captured"
            return explicit_records, audit_trail

        # 2. プロバイダーの診断
        diag = self.diagnose_all_providers(race_id=race_id)
        if diag["all_sources_disconnected"]:
            audit_trail["outcome"] = "all_sources_disconnected"
            audit_trail["diagnostics"] = diag
            return None, audit_trail

        # 3. 接続可能なプロバイダーでの再試行ループ
        for attempt in range(1, max_retries + 1):
            audit_trail["attempts"] = attempt
            for provider in self.providers:
                p_diag = provider.check_connection(race_id=race_id)
                if not p_diag.get("connected"):
                    continue

                try:
                    records = provider.fetch_prerace_odds(race_id, scheduled_race_time, timing_tag)
                    if records:
                        audit_trail["success_source"] = provider.name
                        audit_trail["outcome"] = "captured"
                        return records, audit_trail
                except Exception as e:
                    audit_trail["retry_errors"].append({
                        "attempt": attempt,
                        "provider": provider.name,
                        "error": str(e)
                    })

            # バックオフ待機（最終試行以外）
            if attempt < max_retries:
                time.sleep(backoff_sec * (2 ** (attempt - 1)))

        audit_trail["outcome"] = "retries_exhausted_missing"
        return None, audit_trail
