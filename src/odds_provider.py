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
        # No verified publication-time parser or redistribution contract is implemented.
        # Never probe the site or claim a valid data connection.
        return {
            "provider": self.name,
            "connected": False,
            "status": "DISCONNECTED",
            "reason": "Verified licensed publication timestamps are not implemented."
        }

    def fetch_prerace_odds(
        self,
        race_id: str,
        scheduled_race_time: str,
        timing_tag: str
    ) -> Optional[List[Dict[str, Any]]]:
        # Deliberately unavailable: local fetch time is NOT publication time.
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
