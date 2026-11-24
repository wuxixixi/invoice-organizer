"""配置加载/保存/版本递增、多档案、快照与备份恢复。

配置以 JSON 文件持久化到 ``data_dir/profiles/<profileId>/``：
- ``current.json`` 指向当前版本文件；
- ``v<N>.json`` 保存每一个历史版本，永不删除。

这样历史批次可通过 ``profileVersion`` 回溯当时配置快照（ARCHITECTURE 7.5）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from invoice_organizer.config.schema import ProfileConfig

_DEFAULT_JSON = Path(__file__).with_name("default.json")


class ProfileLoader:
    """管理命名档案的多版本配置。"""

    def __init__(self, data_dir: str | Path) -> None:
        """初始化加载器。

        Args:
            data_dir: 应用数据根目录，配置存放于其下的 profiles/ 子目录。
        """
        self.data_dir = Path(data_dir)
        self.profiles_dir = self.data_dir / "profiles"

    # ---------------------------------------------------------------- 路径
    def _profile_dir(self, profile_id: str) -> Path:
        """返回某档案目录；校验 profileId 防止路径穿越。"""
        safe = "".join(ch for ch in profile_id if ch.isalnum() or ch in "-_")
        if not safe:
            safe = "default"
        return self.profiles_dir / safe

    def _version_path(self, profile_id: str, version: int) -> Path:
        """返回某档案某版本的 JSON 路径。"""
        return self._profile_dir(profile_id) / f"v{version}.json"

    def _current_path(self, profile_id: str) -> Path:
        """返回某档案 current.json 路径。"""
        return self._profile_dir(profile_id) / "current.json"

    # ---------------------------------------------------------------- 默认
    @staticmethod
    def default_config() -> ProfileConfig:
        """从包内 default.json 读取默认配置；文件缺失时退回 dataclass 默认。"""
        try:
            return ProfileConfig.from_json(_DEFAULT_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return ProfileConfig.default()

    # ---------------------------------------------------------------- 加载
    def load(self, profileId: str = "default") -> ProfileConfig:
        """加载档案当前版本配置。

        若档案不存在，写出默认配置（version=1）并返回。若 current.json 损坏，
        回退到最高版本文件；仍失败则返回默认配置。
        """
        pdir = self._profile_dir(profileId)
        current = self._current_path(profileId)
        if current.exists():
            try:
                payload = json.loads(current.read_text(encoding="utf-8"))
                version = int(payload.get("version", 1))
                cfg = ProfileConfig.from_json(
                    self._version_path(profileId, version).read_text(encoding="utf-8")
                )
                cfg.profileId = profileId
                return cfg
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        # 回退：找最高版本
        if pdir.exists():
            versions = sorted(
                (int(p.stem[1:]) for p in pdir.glob("v*.json") if p.stem[1:].isdigit()),
                reverse=True,
            )
            for version in versions:
                try:
                    cfg = ProfileConfig.from_json(
                        self._version_path(profileId, version).read_text(encoding="utf-8")
                    )
                    cfg.profileId = profileId
                    return cfg
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        # 不存在：初始化默认
        cfg = self.default_config()
        cfg.profileId = profileId
        cfg.version = 1
        self._write_version(cfg)
        return cfg

    # ---------------------------------------------------------------- 保存
    def next_version(self, profileId: str) -> int:
        """返回下一次保存应使用的版本号（当前版本 +1）。"""
        current = self._current_path(profileId)
        if current.exists():
            try:
                return int(json.loads(current.read_text(encoding="utf-8")).get("version", 0)) + 1
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        versions = [
            int(p.stem[1:]) for p in self._profile_dir(profileId).glob("v*.json")
            if p.stem[1:].isdigit()
        ]
        return max(versions, default=0) + 1

    def save(self, config: ProfileConfig) -> int:
        """保存配置为新版本（version 自动递增），返回新版本号。

        保存前执行 ``config.validate()``，校验不通过抛出 ``ValueError``（E701/E502）。
        """
        errors = config.validate()
        if errors:
            raise ValueError("; ".join(errors))
        new_version = self.next_version(config.profileId)
        config.version = new_version
        self._write_version(config)
        return new_version

    def _write_version(self, config: ProfileConfig) -> None:
        """把配置写入 vN.json 并更新 current.json 指针。"""
        pdir = self._profile_dir(config.profileId)
        pdir.mkdir(parents=True, exist_ok=True)
        vpath = self._version_path(config.profileId, config.version)
        vpath.write_text(config.to_json(), encoding="utf-8")
        self._current_path(config.profileId).write_text(
            json.dumps({"profileId": config.profileId, "version": config.version},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------------------------------------------------------------- 快照
    @staticmethod
    def snapshot(config: ProfileConfig) -> str:
        """返回配置的不可变全量 JSON 快照（供 batches.config_snapshot 使用）。"""
        return json.dumps(config.to_dict(), ensure_ascii=False, sort_keys=True)

    # ------------------------------------------------------------ 备份恢复
    def backup(self, dest: str | Path) -> str:
        """把整个 profiles 目录复制到 dest，返回备份路径。"""
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if self.profiles_dir.exists():
            if dest_path.exists():
                shutil.rmtree(dest_path)
            shutil.copytree(self.profiles_dir, dest_path)
        else:
            dest_path.mkdir(parents=True, exist_ok=True)
        return str(dest_path.resolve())

    def restore(self, backupPath: str | Path, version: int | None = None,
                profileId: str = "default") -> ProfileConfig:
        """从备份恢复配置。

        Args:
            backupPath: ``backup()`` 生成的目录。
            version: 恢复到的版本号；None 表示恢复该档案 current.json 指向版本。
            profileId: 目标档案 ID。

        Returns:
            恢复后的 ProfileConfig。

        Raises:
            FileNotFoundError: 备份中找不到指定档案/版本。
        """
        src_dir = Path(backupPath) / profileId
        if version is None:
            current = src_dir / "current.json"
            if current.exists():
                version = int(json.loads(current.read_text(encoding="utf-8")).get("version", 1))
            else:
                versions = [int(p.stem[1:]) for p in src_dir.glob("v*.json")
                            if p.stem[1:].isdigit()]
                if not versions:
                    raise FileNotFoundError(f"备份中无档案 {profileId} 的配置")
                version = max(versions)
        src_file = src_dir / f"v{version}.json"
        if not src_file.exists():
            raise FileNotFoundError(f"备份中无 {profileId} 的 v{version}.json")
        cfg = ProfileConfig.from_json(src_file.read_text(encoding="utf-8"))
        cfg.profileId = profileId
        # 恢复到 profiles 目录并作为当前版本
        self._write_version(cfg)
        return cfg


__all__ = ["ProfileLoader"]
