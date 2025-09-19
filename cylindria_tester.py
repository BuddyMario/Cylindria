import json
import uuid
from dataclasses import dataclass
from typing import Optional

import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
from tkinter.scrolledtext import ScrolledText

import httpx


@dataclass
class UIState:
    last_job_id: Optional[str] = None


class CylindriaTesterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Cylindria Tester")

        self.state = UIState()

        # Top frame for URL + Port inputs
        top = tk.Frame(root)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(top, text="Cylindria URL:").grid(row=0, column=0, sticky=tk.W)
        self.entry_url = tk.Entry(top, width=40)
        self.entry_url.grid(row=0, column=1, sticky=tk.W, padx=(4, 8))
        self.entry_url.insert(0, "http://127.0.0.1")

        tk.Label(top, text="Port:").grid(row=0, column=2, sticky=tk.W)
        self.entry_port = tk.Entry(top, width=8)
        self.entry_port.grid(row=0, column=3, sticky=tk.W, padx=(4, 0))
        self.entry_port.insert(0, "8100")

        # Buttons frame
        btns = tk.Frame(root)
        btns.pack(fill=tk.X, padx=8, pady=(0, 4))

        self.btn_server = tk.Button(btns, text="Server status", command=self.on_server_status)
        self.btn_server.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_start = tk.Button(btns, text="Start Job", command=self.on_start_job)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_job = tk.Button(btns, text="Job Status", command=self.on_job_status)
        self.btn_job.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_clear = tk.Button(btns, text="Clear Output", command=self.on_clear_output)
        self.btn_clear.pack(side=tk.LEFT, padx=(0, 6))

        # Output text area
        self.output = ScrolledText(root, height=20, undo=False, wrap=tk.WORD)
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.output.configure(font=("Consolas", 10))

        # HTTP client
        self.client = httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0))

    def base_url(self) -> Optional[str]:
        raw = (self.entry_url.get() or "").strip().rstrip("/")
        port = (self.entry_port.get() or "").strip()
        if not raw:
            messagebox.showerror("Input Error", "Please enter Cylindria URL (e.g. http://127.0.0.1)")
            return None
        if not port.isdigit():
            messagebox.showerror("Input Error", "Please enter a numeric port (e.g. 8000)")
            return None
        return f"{raw}:{port}"

    def log(self, text: str) -> None:
        self.output.insert(tk.END, text + "\n")
        self.output.see(tk.END)

    def on_clear_output(self) -> None:
        self.output.delete('1.0', tk.END)

    def log_json(self, title: str, data) -> None:
        self.log(f"=== {title} ===")
        try:
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
        except Exception:
            pretty = str(data)
        self.log(pretty)
        self.log("")

    def on_server_status(self) -> None:
        base = self.base_url()
        if not base:
            return
        url = f"{base}/serverstatus"
        self.log(f"GET {url}")
        try:
            r = self.client.get(url)
            self.log(f"HTTP {r.status_code}")
            self.log_json("ServerStatus", r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
        except httpx.HTTPError as e:
            messagebox.showerror("Request Error", f"Failed to reach server: {e}")
            self.log(f"Error: {e}")

    def on_start_job(self) -> None:
        base = self.base_url()
        if not base:
            return
        file_path = filedialog.askopenfilename(
            title="Select ComfyUI Workflow (JSON)",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                workflow = json.load(f)
        except Exception as e:
            messagebox.showerror("File Error", f"Failed to read JSON: {e}")
            return

        job_id = uuid.uuid4().hex
        url = f"{base}/startjob/{job_id}/"
        self.log(f"PUT {url}\nBody: workflow from {file_path}")
        try:
            r = self.client.put(url, json=workflow)
            self.log(f"HTTP {r.status_code}")
            payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            self.log_json("StartJob Response", payload)
            self.state.last_job_id = job_id
            self.log(f"Saved last job id: {job_id}\n")
        except httpx.HTTPError as e:
            messagebox.showerror("Request Error", f"Failed to send job: {e}")
            self.log(f"Error: {e}")

    def on_job_status(self) -> None:
        base = self.base_url()
        if not base:
            return
        default = self.state.last_job_id or ""
        job_id = self.ask_wide_string("Job Status", "Enter Job ID:", initialvalue=default, width=60)
        if not job_id:
            return
        job_id = job_id.strip()
        url = f"{base}/jobstatus/{job_id}/"
        self.log(f"GET {url}")
        try:
            r = self.client.get(url)
            self.log(f"HTTP {r.status_code}")
            payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            self.log_json("JobStatus Response", payload)
            self.state.last_job_id = job_id
        except httpx.HTTPError as e:
            messagebox.showerror("Request Error", f"Failed to query status: {e}")
            self.log(f"Error: {e}")

    def ask_wide_string(self, title: str, prompt: str, initialvalue: str = "", width: int = 60) -> Optional[str]:
        class WideEntryDialog(simpledialog.Dialog):
            def __init__(self, parent, title, prompt, initial, width):
                self._prompt = prompt
                self._initial = initial
                self._width = width
                super().__init__(parent, title)

            def body(self, master):
                tk.Label(master, text=self._prompt).grid(row=0, column=0, sticky="w")
                self.entry = tk.Entry(master, width=self._width)
                self.entry.grid(row=1, column=0, sticky="we", pady=(4, 0))
                if self._initial:
                    self.entry.insert(0, self._initial)
                    self.entry.select_range(0, tk.END)
                return self.entry

            def apply(self):
                self.result = self.entry.get().strip()

        dlg = WideEntryDialog(self.root, title, prompt, initialvalue, width)
        return getattr(dlg, "result", None)


def main() -> None:
    root = tk.Tk()
    app = CylindriaTesterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
