# -*- coding: utf-8 -*-
"""Admin: accounts, proxies, packages (ký tự), max workers ≤ 5."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QHeaderView

import accounts_store as store


class AdminPanel(QtWidgets.QWidget):
    def __init__(self, parent, current_user: dict, on_changed: Optional[Callable] = None):
        super().__init__(parent)
        self.current_user = current_user
        self.on_changed = on_changed
        self._setup_ui()
        self.reload_all()

    def _setup_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        title = QtWidgets.QLabel("Quản trị local (account · proxy · gói ký tự · luồng)")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        root.addWidget(title)

        tabs = QtWidgets.QTabWidget()
        root.addWidget(tabs, 1)

        # ── Accounts ──
        acc = QtWidgets.QWidget()
        al = QtWidgets.QVBoxLayout(acc)
        self.tbl_acc = QtWidgets.QTableWidget(0, 9)
        self.tbl_acc.setHorizontalHeaderLabels(
            [
                "User",
                "Role",
                "Gói",
                "Đã dùng",
                "Quota",
                "Còn",
                "Max luồng",
                "Hội thoại",
                "Proxy",
            ]
        )
        self.tbl_acc.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_acc.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_acc.verticalHeader().setVisible(False)
        self.tbl_acc.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_acc.setAlternatingRowColors(True)
        al.addWidget(self.tbl_acc, 1)

        form = QtWidgets.QGridLayout()
        self.ed_user = QtWidgets.QLineEdit()
        self.ed_user.setPlaceholderText("username")
        self.ed_pass = QtWidgets.QLineEdit()
        self.ed_pass.setPlaceholderText("password (tạo/đổi)")
        self.ed_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.cb_role = QtWidgets.QComboBox()
        self.cb_role.addItems(["user", "admin"])
        self.cb_pkg = QtWidgets.QComboBox()
        self.sb_workers = QtWidgets.QSpinBox()
        self.sb_workers.setRange(1, store.MAX_WORKERS_HARD)
        self.sb_workers.setValue(2)
        self.cb_proxy = QtWidgets.QComboBox()
        self.ed_note = QtWidgets.QLineEdit()
        self.ed_note.setPlaceholderText("ghi chú")
        self.chk_enabled = QtWidgets.QCheckBox("Enabled")
        self.chk_enabled.setChecked(True)
        self.chk_multivoice = QtWidgets.QCheckBox("Tab Hội thoại (premium)")
        self.chk_multivoice.setChecked(False)
        form.addWidget(QtWidgets.QLabel("User"), 0, 0)
        form.addWidget(self.ed_user, 0, 1)
        form.addWidget(QtWidgets.QLabel("Pass"), 0, 2)
        form.addWidget(self.ed_pass, 0, 3)
        form.addWidget(QtWidgets.QLabel("Role"), 1, 0)
        form.addWidget(self.cb_role, 1, 1)
        form.addWidget(QtWidgets.QLabel("Gói ký tự"), 1, 2)
        form.addWidget(self.cb_pkg, 1, 3)
        form.addWidget(QtWidgets.QLabel("Max luồng"), 2, 0)
        form.addWidget(self.sb_workers, 2, 1)
        form.addWidget(QtWidgets.QLabel("Proxy"), 2, 2)
        form.addWidget(self.cb_proxy, 2, 3)
        form.addWidget(self.ed_note, 3, 0, 1, 2)
        form.addWidget(self.chk_enabled, 3, 2)
        form.addWidget(self.chk_multivoice, 3, 3)
        al.addLayout(form)

        brow = QtWidgets.QHBoxLayout()
        self.bt_acc_new = QtWidgets.QPushButton("Tạo account")
        self.bt_acc_save = QtWidgets.QPushButton("Lưu chọn")
        self.bt_acc_del = QtWidgets.QPushButton("Xóa")
        self.bt_acc_reset = QtWidgets.QPushButton("Reset đã dùng = 0")
        self.bt_acc_new.setObjectName("primaryButton")
        brow.addWidget(self.bt_acc_new)
        brow.addWidget(self.bt_acc_save)
        brow.addWidget(self.bt_acc_del)
        brow.addWidget(self.bt_acc_reset)
        brow.addStretch(1)
        al.addLayout(brow)
        tabs.addTab(acc, "Account")

        # ── Proxies ──
        px = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(px)
        self.tbl_px = QtWidgets.QTableWidget(0, 5)
        self.tbl_px.setHorizontalHeaderLabels(
            ["ID", "Label", "Host:Port", "User", "Enabled"]
        )
        self.tbl_px.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_px.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_px.verticalHeader().setVisible(False)
        self.tbl_px.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_px.setAlternatingRowColors(True)
        pl.addWidget(self.tbl_px, 1)

        pf = QtWidgets.QGridLayout()
        self.ed_px_id = QtWidgets.QLineEdit()
        self.ed_px_id.setPlaceholderText("id (để trống = auto)")
        self.ed_px_label = QtWidgets.QLineEdit()
        self.ed_px_label.setPlaceholderText("label")
        self.ed_px_key = QtWidgets.QLineEdit()
        self.ed_px_key.setPlaceholderText("API key đường truyền")
        self.ed_px_user = QtWidgets.QLineEdit()
        self.ed_px_user.setPlaceholderText("username")
        self.ed_px_pass = QtWidgets.QLineEdit()
        self.ed_px_pass.setPlaceholderText("password")
        self.ed_px_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ed_px_host = QtWidgets.QLineEdit()
        self.ed_px_host.setPlaceholderText("máy chủ")
        self.sb_px_port = QtWidgets.QSpinBox()
        self.sb_px_port.setRange(1, 65535)
        self.sb_px_port.setValue(8978)
        self.chk_px_en = QtWidgets.QCheckBox("Enabled")
        self.chk_px_en.setChecked(True)
        pf.addWidget(self.ed_px_id, 0, 0)
        pf.addWidget(self.ed_px_label, 0, 1)
        pf.addWidget(self.ed_px_key, 0, 2)
        pf.addWidget(self.ed_px_user, 1, 0)
        pf.addWidget(self.ed_px_pass, 1, 1)
        pf.addWidget(self.ed_px_host, 1, 2)
        pf.addWidget(self.sb_px_port, 2, 0)
        pf.addWidget(self.chk_px_en, 2, 1)
        pl.addLayout(pf)
        pr = QtWidgets.QHBoxLayout()
        self.bt_px_save = QtWidgets.QPushButton("Lưu proxy")
        self.bt_px_del = QtWidgets.QPushButton("Xóa proxy")
        self.bt_px_save.setObjectName("primaryButton")
        pr.addWidget(self.bt_px_save)
        pr.addWidget(self.bt_px_del)
        pr.addStretch(1)
        pl.addLayout(pr)
        tabs.addTab(px, "Proxy")

        # ── Packages ──
        pkg = QtWidgets.QWidget()
        kl = QtWidgets.QVBoxLayout(pkg)
        self.tbl_pkg = QtWidgets.QTableWidget(0, 3)
        self.tbl_pkg.setHorizontalHeaderLabels(["ID", "Tên gói", "Ký tự"])
        self.tbl_pkg.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_pkg.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_pkg.verticalHeader().setVisible(False)
        self.tbl_pkg.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        kl.addWidget(self.tbl_pkg, 1)
        kf = QtWidgets.QHBoxLayout()
        self.ed_pkg_name = QtWidgets.QLineEdit()
        self.ed_pkg_name.setPlaceholderText("Tên gói")
        self.sb_pkg_chars = QtWidgets.QSpinBox()
        self.sb_pkg_chars.setRange(1000, 500_000_000)
        self.sb_pkg_chars.setSingleStep(1_000_000)
        self.sb_pkg_chars.setValue(1_000_000)
        self.sb_pkg_chars.setSuffix(" ký tự")
        self.bt_pkg_save = QtWidgets.QPushButton("Thêm / cập nhật gói")
        self.bt_pkg_del = QtWidgets.QPushButton("Xóa gói")
        self.bt_pkg_save.setObjectName("primaryButton")
        kf.addWidget(self.ed_pkg_name, 2)
        kf.addWidget(self.sb_pkg_chars, 2)
        kf.addWidget(self.bt_pkg_save)
        kf.addWidget(self.bt_pkg_del)
        kl.addLayout(kf)
        hint = QtWidgets.QLabel(
            "Ví dụ: 1.000.000 = 1 triệu · 10.000.000 = 10 triệu · max luồng account ≤ 5"
        )
        hint.setStyleSheet("color:#737373; font-size:11px;")
        kl.addWidget(hint)
        tabs.addTab(pkg, "Gói ký tự")

        self.setStyleSheet(
            """
            QPushButton#primaryButton {
                color: #fff; background: #171717; border: 1px solid #171717;
                border-radius: 6px; padding: 6px 12px;
            }
            QPushButton {
                border: 1px solid #c9c9c9; border-radius: 6px; padding: 6px 12px;
                background: #fff;
            }
            QTableWidget {
                border: 1px solid #d4d4d4; border-radius: 6px;
                alternate-background-color: #f7f7f7;
            }
            QLineEdit, QSpinBox, QComboBox {
                border: 1px solid #c9c9c9; border-radius: 6px; padding: 6px;
            }
            """
        )

        self.tbl_acc.itemSelectionChanged.connect(self._fill_acc_form)
        self.tbl_px.itemSelectionChanged.connect(self._fill_px_form)
        self.tbl_pkg.itemSelectionChanged.connect(self._fill_pkg_form)
        self.bt_acc_new.clicked.connect(self._acc_new)
        self.bt_acc_save.clicked.connect(self._acc_save)
        self.bt_acc_del.clicked.connect(self._acc_del)
        self.bt_acc_reset.clicked.connect(self._acc_reset_usage)
        self.bt_px_save.clicked.connect(self._px_save)
        self.bt_px_del.clicked.connect(self._px_del)
        self.bt_pkg_save.clicked.connect(self._pkg_save)
        self.bt_pkg_del.clicked.connect(self._pkg_del)

        self._acc_ids: list[str] = []
        self._px_ids: list[str] = []
        self._pkg_ids: list[str] = []

    def reload_all(self):
        store.ensure_default_packages()
        self._reload_packages_combo()
        self._reload_proxy_combo()
        self._reload_accounts()
        self._reload_proxies_table()
        self._reload_packages_table()

    def _reload_packages_combo(self):
        self.cb_pkg.blockSignals(True)
        self.cb_pkg.clear()
        self.cb_pkg.addItem("— tuỳ chỉnh —", "")
        for p in store.list_packages():
            label = f"{p.get('name')} ({int(p.get('chars') or 0):,} ký tự)"
            self.cb_pkg.addItem(label, p.get("id"))
        self.cb_pkg.blockSignals(False)

    def _reload_proxy_combo(self):
        self.cb_proxy.blockSignals(True)
        self.cb_proxy.clear()
        self.cb_proxy.addItem("— không —", "")
        for p in store.list_proxies():
            if not p.get("enabled", True):
                continue
            # need full list with enabled - list_proxies returns raw
            pass
        data = store._read(store.PROXIES_FILE, {"proxies": []})
        for p in data.get("proxies") or []:
            if not p.get("enabled", True):
                continue
            lab = p.get("label") or p.get("id")
            self.cb_proxy.addItem(
                f"{lab} ({p.get('host')}:{p.get('port')})", p.get("id")
            )
        self.cb_proxy.blockSignals(False)

    def _reload_accounts(self):
        rows = store.list_accounts()
        self._acc_ids = [r["id"] for r in rows]
        self.tbl_acc.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [
                r.get("username"),
                r.get("role"),
                r.get("package_name") or "—",
                f"{int(r.get('chars_used') or 0):,}",
                f"{int(r.get('char_quota') or 0):,}",
                f"{int(r.get('chars_left') or 0):,}",
                str(r.get("max_workers")),
                "ON" if r.get("multivoice_enabled") else "OFF",
                "yes" if r.get("has_proxy") else "no",
            ]
            for c, v in enumerate(vals):
                self.tbl_acc.setItem(i, c, QtWidgets.QTableWidgetItem(str(v)))

    def _reload_proxies_table(self):
        data = store._read(store.PROXIES_FILE, {"proxies": []})
        rows = data.get("proxies") or []
        self._px_ids = [p.get("id") for p in rows]
        self.tbl_px.setRowCount(len(rows))
        for i, p in enumerate(rows):
            vals = [
                p.get("id"),
                p.get("label"),
                f"{p.get('host')}:{p.get('port')}",
                p.get("username"),
                "yes" if p.get("enabled", True) else "no",
            ]
            for c, v in enumerate(vals):
                self.tbl_px.setItem(i, c, QtWidgets.QTableWidgetItem(str(v or "")))

    def _reload_packages_table(self):
        rows = store.list_packages()
        self._pkg_ids = [p.get("id") for p in rows]
        self.tbl_pkg.setRowCount(len(rows))
        for i, p in enumerate(rows):
            self.tbl_pkg.setItem(i, 0, QtWidgets.QTableWidgetItem(str(p.get("id"))))
            self.tbl_pkg.setItem(i, 1, QtWidgets.QTableWidgetItem(str(p.get("name"))))
            self.tbl_pkg.setItem(
                i, 2, QtWidgets.QTableWidgetItem(f"{int(p.get('chars') or 0):,}")
            )

    def _selected_acc_id(self) -> Optional[str]:
        r = self.tbl_acc.currentRow()
        if r < 0 or r >= len(self._acc_ids):
            return None
        return self._acc_ids[r]

    def _fill_acc_form(self):
        aid = self._selected_acc_id()
        if not aid:
            return
        a = store.get_account(aid)
        if not a:
            return
        self.ed_user.setText(a.get("username") or "")
        self.ed_pass.clear()
        self.cb_role.setCurrentText(a.get("role") or "user")
        self.sb_workers.setValue(int(a.get("max_workers") or 2))
        self.ed_note.setText(a.get("note") or "")
        self.chk_enabled.setChecked(bool(a.get("enabled", True)))
        self.chk_multivoice.setChecked(bool(a.get("multivoice_enabled")))
        # package
        pid = a.get("package_id") or ""
        idx = self.cb_pkg.findData(pid)
        self.cb_pkg.setCurrentIndex(max(0, idx))
        # proxy
        px = a.get("proxy_id") or ""
        idx = self.cb_proxy.findData(px)
        self.cb_proxy.setCurrentIndex(max(0, idx))

    def _fill_px_form(self):
        r = self.tbl_px.currentRow()
        if r < 0 or r >= len(self._px_ids):
            return
        p = store.get_proxy(self._px_ids[r])
        if not p:
            return
        self.ed_px_id.setText(p.get("id") or "")
        self.ed_px_label.setText(p.get("label") or "")
        self.ed_px_key.setText(p.get("api_key") or "")
        self.ed_px_user.setText(p.get("username") or "")
        self.ed_px_pass.setText(p.get("password") or "")
        self.ed_px_host.setText(p.get("host") or "")
        self.sb_px_port.setValue(int(p.get("port") or 8978))
        self.chk_px_en.setChecked(bool(p.get("enabled", True)))

    def _fill_pkg_form(self):
        r = self.tbl_pkg.currentRow()
        if r < 0 or r >= len(self._pkg_ids):
            return
        for p in store.list_packages():
            if p.get("id") == self._pkg_ids[r]:
                self.ed_pkg_name.setText(p.get("name") or "")
                self.sb_pkg_chars.setValue(int(p.get("chars") or 1_000_000))
                break

    def _acc_new(self):
        try:
            pkg_id = self.cb_pkg.currentData() or ""
            pkg_name = ""
            quota = 1_000_000
            if pkg_id:
                for p in store.list_packages():
                    if p.get("id") == pkg_id:
                        pkg_name = p.get("name") or ""
                        quota = int(p.get("chars") or quota)
                        break
            row = store.create_account(
                username=self.ed_user.text().strip(),
                password=self.ed_pass.text() or "123456",
                note=self.ed_note.text().strip(),
                role=self.cb_role.currentText(),
                char_quota=quota,
                max_workers=self.sb_workers.value(),
                package_id=pkg_id or "",
                package_name=pkg_name,
                proxy_id=self.cb_proxy.currentData() or "",
            )
            if row and row.get("id"):
                store.update_account(
                    row["id"],
                    multivoice_enabled=self.chk_multivoice.isChecked(),
                )
            self.reload_all()
            if self.on_changed:
                self.on_changed()
            QtWidgets.QMessageBox.information(self, "OK", "Đã tạo account")
        except Exception as e:
            try:
                from user_safe import sanitize_user_error

                QtWidgets.QMessageBox.warning(
                    self, "Lỗi", sanitize_user_error(e, fallback="Thao tác thất bại.")
                )
            except Exception:
                QtWidgets.QMessageBox.warning(self, "Lỗi", "Thao tác thất bại.")

    def _acc_save(self):
        aid = self._selected_acc_id()
        if not aid:
            QtWidgets.QMessageBox.warning(self, "Lỗi", "Chọn account trong bảng")
            return
        fields = {
            "note": self.ed_note.text().strip(),
            "role": self.cb_role.currentText(),
            "enabled": self.chk_enabled.isChecked(),
            "max_workers": self.sb_workers.value(),
            "proxy_id": self.cb_proxy.currentData() or "",
            "multivoice_enabled": self.chk_multivoice.isChecked(),
        }
        if self.ed_pass.text():
            fields["password"] = self.ed_pass.text()
        pkg_id = self.cb_pkg.currentData() or ""
        if pkg_id:
            fields["package_id"] = pkg_id
        else:
            # keep quota as-is unless package selected
            pass
        store.update_account(aid, **fields)
        self.reload_all()
        if self.on_changed:
            self.on_changed()
        QtWidgets.QMessageBox.information(self, "OK", "Đã lưu account")

    def _acc_del(self):
        aid = self._selected_acc_id()
        if not aid:
            return
        a = store.get_account(aid)
        if a and a.get("username") == self.current_user.get("username"):
            QtWidgets.QMessageBox.warning(self, "Lỗi", "Không xóa account đang login")
            return
        if (
            QtWidgets.QMessageBox.question(self, "Xóa?", f"Xóa account {a and a.get('username')}?")
            != QtWidgets.QMessageBox.Yes
        ):
            return
        store.delete_account(aid)
        self.reload_all()

    def _acc_reset_usage(self):
        aid = self._selected_acc_id()
        if not aid:
            return
        store.update_account(aid, chars_used=0)
        self.reload_all()
        if self.on_changed:
            self.on_changed()

    def _px_save(self):
        try:
            store.save_proxy(
                {
                    "id": self.ed_px_id.text().strip() or None,
                    "label": self.ed_px_label.text().strip(),
                    "api_key": self.ed_px_key.text().strip(),
                    "username": self.ed_px_user.text().strip(),
                    "password": self.ed_px_pass.text(),
                    "host": self.ed_px_host.text().strip(),
                    "port": self.sb_px_port.value(),
                    "enabled": self.chk_px_en.isChecked(),
                    "provider": "proxyxoay_net",
                }
            )
            self.reload_all()
            QtWidgets.QMessageBox.information(self, "OK", "Đã lưu proxy")
        except Exception as e:
            try:
                from user_safe import sanitize_user_error

                QtWidgets.QMessageBox.warning(
                    self, "Lỗi", sanitize_user_error(e, fallback="Thao tác thất bại.")
                )
            except Exception:
                QtWidgets.QMessageBox.warning(self, "Lỗi", "Thao tác thất bại.")

    def _px_del(self):
        r = self.tbl_px.currentRow()
        if r < 0 or r >= len(self._px_ids):
            return
        store.delete_proxy(self._px_ids[r])
        self.reload_all()

    def _pkg_save(self):
        name = self.ed_pkg_name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Lỗi", "Nhập tên gói")
            return
        pid = None
        r = self.tbl_pkg.currentRow()
        if r >= 0 and r < len(self._pkg_ids):
            pid = self._pkg_ids[r]
        store.save_package(
            {
                "id": pid,
                "name": name,
                "chars": self.sb_pkg_chars.value(),
                "note": f"{self.sb_pkg_chars.value():,} ký tự",
            }
        )
        self.reload_all()
        QtWidgets.QMessageBox.information(self, "OK", "Đã lưu gói")

    def _pkg_del(self):
        r = self.tbl_pkg.currentRow()
        if r < 0 or r >= len(self._pkg_ids):
            return
        store.delete_package(self._pkg_ids[r])
        self.reload_all()
