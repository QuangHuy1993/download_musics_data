const excelFileInput = document.getElementById("excelFile");
const excelElectronWrapper = document.getElementById("excelElectronWrapper");
const excelBrowseBtn = document.getElementById("excelBrowseBtn");
const excelPathText = document.getElementById("excelPathText");

const outputDirInput = document.getElementById("outputDir");
const outputElectronWrapper = document.getElementById("outputElectronWrapper");
const outputBrowseBtn = document.getElementById("outputBrowseBtn");
const outputPathText = document.getElementById("outputPathText");

const sheetApiUrlInput = document.getElementById("sheetApiUrl");
const sheetTokenInput = document.getElementById("sheetToken");
const workersInput = document.getElementById("workers");
const maxItemsInput = document.getElementById("maxItems");
const startRowInput = document.getElementById("startRow");
const endRowInput = document.getElementById("endRow");

const cookieBadge = document.getElementById("cookieBadge");
const extractChromeBtn = document.getElementById("extractChromeBtn");
const extractEdgeBtn = document.getElementById("extractEdgeBtn");
const extractSafariBtn = document.getElementById("extractSafariBtn");
const extractFirefoxBtn = document.getElementById("extractFirefoxBtn");
const cookiesText = document.getElementById("cookiesText");
const saveCookiesBtn = document.getElementById("saveCookiesBtn");

const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const flushBtn = document.getElementById("flushBtn");
const resetDbBtn = document.getElementById("resetDbBtn");
const clearLogsBtn = document.getElementById("clearLogsBtn");

const health = document.getElementById("health");
const stateEl = document.getElementById("state");
const counterEl = document.getElementById("counter");
const rateEl = document.getElementById("rate");
const barEl = document.getElementById("bar");

const statDone = document.getElementById("stat-done");
const statFailed = document.getElementById("stat-failed");
const statPending = document.getElementById("stat-pending");
const logsEl = document.getElementById("logs");

let pollTimer = null;
const isElectron = Boolean(window.electronAPI);

init();

async function init() {
  // Load saved preferences
  const saved = JSON.parse(localStorage.getItem("melonBatchPrefs") || "{}");
  if (saved.sheetApiUrl) sheetApiUrlInput.value = saved.sheetApiUrl;
  sheetTokenInput.value = saved.sheetToken || "";
  if (saved.workers) workersInput.value = saved.workers;
  if (saved.maxItems) maxItemsInput.value = saved.maxItems;
  if (saved.startRow) startRowInput.value = saved.startRow;
  if (saved.endRow) endRowInput.value = saved.endRow;

  if (isElectron) {
    // Show Electron controls and hide standard fallbacks
    excelElectronWrapper.classList.remove("hidden");
    excelFileInput.classList.add("hidden");
    outputElectronWrapper.classList.remove("hidden");
    outputDirInput.classList.add("hidden");

    // Load saved file/folder paths
    const savedExcelPath = localStorage.getItem("excelPath") || "";
    const savedOutputPath = localStorage.getItem("outputPath") || "output";

    if (savedExcelPath) {
      excelPathText.textContent = getFileName(savedExcelPath);
      excelPathText.title = savedExcelPath;
      // Auto import local Excel file on start
      importLocalExcel(savedExcelPath);
    }
    outputPathText.textContent = savedOutputPath;
    outputPathText.title = savedOutputPath;
  } else {
    // Standard web page fallback
    excelElectronWrapper.classList.add("hidden");
    excelFileInput.classList.remove("hidden");
    outputElectronWrapper.classList.add("hidden");
    outputDirInput.classList.remove("hidden");
    
    if (saved.outputDir) outputDirInput.value = saved.outputDir;
  }

  // Setup click listeners for browse buttons (Electron only)
  if (isElectron) {
    excelBrowseBtn.addEventListener("click", selectExcelFile);
    outputBrowseBtn.addEventListener("click", selectOutputDir);
  } else {
    excelFileInput.addEventListener("change", importUploadedExcel);
  }

  startBtn.addEventListener("click", startJob);
  stopBtn.addEventListener("click", stopJob);
  flushBtn.addEventListener("click", flushSheet);
  clearLogsBtn.addEventListener("click", async () => {
    try {
      await fetchJson("/api/clear_logs", { method: "POST" });
      logsEl.innerHTML = '<li class="log-info">Nhật ký đã được xóa.</li>';
    } catch (error) {
      console.error(error);
    }
  });

  resetDbBtn.addEventListener("click", async () => {
    if (!confirm("Bạn có chắc chắn muốn xóa toàn bộ lịch sử chạy và đưa các chỉ số tiến trình về 0?")) {
      return;
    }
    try {
      await fetchJson("/api/reset_db", { method: "POST" });
      logsEl.innerHTML = '<li class="log-info">Đã reset cơ sở dữ liệu và nhật ký thành công.</li>';
      await refreshStatus();
    } catch (error) {
      pushLog(`Lỗi reset dữ liệu: ${error.message}`, "error");
    }
  });

  extractChromeBtn.addEventListener("click", () => extractBrowserCookies("chrome"));
  extractEdgeBtn.addEventListener("click", () => extractBrowserCookies("edge"));
  extractSafariBtn.addEventListener("click", () => extractBrowserCookies("safari"));
  extractFirefoxBtn.addEventListener("click", () => extractBrowserCookies("firefox"));
  saveCookiesBtn.addEventListener("click", saveCookies);

  await loadCookies();
  await refreshStatus();
  pollTimer = setInterval(refreshStatus, 1500);
}

// File and folder selection (Electron Dialogs)
async function selectExcelFile() {
  try {
    const path = await window.electronAPI.selectFile();
    if (path) {
      localStorage.setItem("excelPath", path);
      excelPathText.textContent = getFileName(path);
      excelPathText.title = path;
      pushLog(`Đã chọn file: ${getFileName(path)}`, "info");
      await importLocalExcel(path);
    }
  } catch (error) {
    pushLog(`Lỗi chọn file: ${error.message}`, "error");
  }
}

async function selectOutputDir() {
  try {
    const path = await window.electronAPI.selectDirectory();
    if (path) {
      localStorage.setItem("outputPath", path);
      outputPathText.textContent = path;
      outputPathText.title = path;
      pushLog(`Đã chọn thư mục lưu nhạc: ${path}`, "info");
    }
  } catch (error) {
    pushLog(`Lỗi chọn thư mục: ${error.message}`, "error");
  }
}

// Import APIs
async function importLocalExcel(path) {
  setBusy(true);
  try {
    pushLog("Đang nạp file Excel...", "info");
    const result = await fetchJson("/api/import_local_excel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path })
    });
    pushLog(`Nạp xong: ${result.inserted} mới, ${result.skipped} trùng, ${result.rows} dòng hợp lệ.`, "success");
    await refreshStatus();
  } catch (error) {
    pushLog(`Lỗi nạp Excel: ${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

async function importUploadedExcel() {
  const file = excelFileInput.files?.[0];
  if (!file) return;

  setBusy(true);
  try {
    pushLog("Đang tải lên file Excel...", "info");
    const dataBase64 = await fileToBase64(file);
    const result = await fetchJson("/api/import_excel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, dataBase64 })
    });
    pushLog(`Tải lên xong: ${result.inserted} mới, ${result.skipped} trùng, ${result.rows} dòng hợp lệ.`, "success");
    await refreshStatus();
  } catch (error) {
    pushLog(`Lỗi tải lên Excel: ${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

// Execution Jobs
async function startJob() {
  const excelPath = localStorage.getItem("excelPath");
  if (isElectron && !excelPath) {
    pushLog("Vui lòng chọn file Excel trước khi bắt đầu.", "warning");
    return;
  }

  const outputDir = isElectron 
    ? (localStorage.getItem("outputPath") || "output") 
    : (outputDirInput.value.trim() || "output");

  const prefs = {
    sheetApiUrl: sheetApiUrlInput.value.trim(),
    sheetToken: sheetTokenInput.value.trim(),
    workers: Number.parseInt(workersInput.value, 10) || 10,
    maxItems: Number.parseInt(maxItemsInput.value, 10) || 0,
    startRow: Number.parseInt(startRowInput.value, 10) || 0,
    endRow: Number.parseInt(endRowInput.value, 10) || 0
  };

  if (prefs.startRow && prefs.endRow && prefs.startRow > prefs.endRow) {
    pushLog("Row bắt đầu phải nhỏ hơn hoặc bằng row kết thúc.", "error");
    return;
  }
  
  // Save settings in localStorage
  localStorage.setItem("melonBatchPrefs", JSON.stringify({
    ...prefs,
    outputDir: isElectron ? "" : outputDir
  }));

  setBusy(true);
  try {
    if (prefs.sheetApiUrl && prefs.sheetToken) {
      await fetchJson("/api/configure_sheet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apiUrl: prefs.sheetApiUrl, token: prefs.sheetToken })
      });
    }

    await fetchJson("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        outputDir,
        workers: prefs.workers,
        maxItems: prefs.maxItems,
        startRow: prefs.startRow,
        endRow: prefs.endRow
      })
    });
    pushLog("Đã khởi chạy tiến trình tải nhạc.", "info");
  } catch (error) {
    pushLog(`Lỗi khởi chạy: ${error.message}`, "error");
  } finally {
    setBusy(false);
    await refreshStatus();
  }
}

async function stopJob() {
  try {
    await fetchJson("/api/stop", { method: "POST" });
    pushLog("Đã gửi yêu cầu dừng sau khi hoàn thành batch hiện tại.", "warning");
  } catch (error) {
    pushLog(`Lỗi dừng: ${error.message}`, "error");
  }
}

async function flushSheet() {
  try {
    const result = await fetchJson("/api/flush_sheet", { method: "POST" });
    pushLog(`Đã đồng bộ thành công ${result.result.sent || 0} dòng lên Google Sheet.`, "success");
  } catch (error) {
    pushLog(`Lỗi đồng bộ Sheet: ${error.message}`, "error");
  }
}

// Status Polling
async function refreshStatus() {
  try {
    const data = await fetchJson("/api/status");
    renderStatus(data);
    const healthData = await fetchJson("/api/health");
    renderHealth(healthData);
  } catch (error) {
    health.textContent = "Không có kết nối server";
    health.className = "status-pill status-error";
  }
}

function renderHealth(data) {
  const ready = Boolean(data.ytDlp && data.ffmpeg);
  health.textContent = ready ? "yt-dlp/ffmpeg Sẵn sàng" : "Thiếu yt-dlp/ffmpeg";
  health.className = ready ? "status-pill status-ok" : "status-pill status-error";
}

function renderStatus(data) {
  const stats = data.stats || {};
  const runner = data.runner || {};
  const total = Number(stats.total || 0);
  const done = Number(stats.done || 0);
  const failed = Number(stats.failed || 0);
  const pending = Number(stats.pending || 0);
  const running = Number(stats.running || 0);
  const pausedYoutube = Number(stats.paused_youtube || 0);
  const completed = done + failed;

  // Header status banner
  if (runner.running) {
    stateEl.textContent = "Đang chạy";
    stateEl.className = "stat-value text-pending";
  } else {
    stateEl.textContent = "Sẵn sàng";
    stateEl.className = "stat-value state-idle";
  }
  
  counterEl.textContent = `${completed} / ${total}`;
  rateEl.textContent = `${runner.ratePerHour || 0} bài/giờ · ${runner.elapsedSeconds || 0} giây`;
  barEl.style.width = total ? `${Math.round((completed / total) * 100)}%` : "0%";
  
  // Stats grid badges
  statDone.textContent = done;
  statFailed.textContent = failed;
  statPending.textContent = pending + running + pausedYoutube;

  // Append new logs in console
  const logsList = runner.logs || [];
  if (logsList.length > 0) {
    logsEl.innerHTML = "";
    for (const item of logsList.slice().reverse()) {
      const li = document.createElement("li");
      const timeStr = new Date(item.time * 1000).toLocaleTimeString("vi-VN", { hour12: false });
      li.textContent = `[${timeStr}] ${item.message}`;
      
      // Syntax coloring for logs
      if (item.message.toLowerCase().includes("lỗi") || item.message.toLowerCase().includes("failed") || item.message.toLowerCase().includes("error")) {
        li.className = "log-error";
      } else if (item.message.toLowerCase().includes("xong") || item.message.toLowerCase().includes("hoàn tất") || item.message.toLowerCase().includes("success")) {
        li.className = "log-success";
      } else if (item.message.toLowerCase().includes("bắt đầu") || item.message.toLowerCase().includes("chạy")) {
        li.className = "log-warning";
      } else {
        li.className = "log-info";
      }
      logsEl.appendChild(li);
    }
  }
}

// Cookie Actions
async function loadCookies() {
  try {
    const data = await fetchJson("/api/cookies");
    const content = data.cookies || "";
    cookiesText.value = content;
    updateCookieBadge(content);
  } catch (error) {
    console.error("Lỗi nạp cookies:", error);
  }
}

function updateCookieBadge(content) {
  if (content.trim()) {
    cookieBadge.textContent = "Có cookie";
    cookieBadge.className = "badge badge-active";
  } else {
    cookieBadge.textContent = "Không có cookie";
    cookieBadge.className = "badge badge-none";
  }
}

async function saveCookies() {
  const content = cookiesText.value.trim();
  setBusy(true);
  try {
    await fetchJson("/api/save_cookies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookies: content })
    });
    updateCookieBadge(content);
    pushLog("Đã cập nhật cookies thành công.", "success");
  } catch (error) {
    pushLog(`Lỗi lưu cookies: ${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

async function extractBrowserCookies(browserName) {
  setBusy(true);
  pushLog(`Đang trích xuất cookies từ trình duyệt ${browserName.toUpperCase()} (vui lòng chờ)...`, "warning");
  try {
    const res = await fetchJson("/api/extract_browser_cookies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ browser: browserName })
    });
    pushLog(res.message, "success");
    await loadCookies();
  } catch (error) {
    pushLog(`Lỗi trích xuất cookies từ ${browserName.toUpperCase()}: ${error.message}`, "error");
  } finally {
    setBusy(false);
  }
}

// Helpers
function getFileName(filePath) {
  if (!filePath) return "Chưa chọn file...";
  return filePath.split(/[\\/]/).pop();
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",")[1] : value);
    };
    reader.readAsDataURL(file);
  });
}

function setBusy(isBusy) {
  startBtn.disabled = isBusy;
  extractChromeBtn.disabled = isBusy;
  extractEdgeBtn.disabled = isBusy;
  extractSafariBtn.disabled = isBusy;
  extractFirefoxBtn.disabled = isBusy;
  saveCookiesBtn.disabled = isBusy;
  resetDbBtn.disabled = isBusy;
}

function pushLog(message, type = "info") {
  const li = document.createElement("li");
  const timeStr = new Date().toLocaleTimeString("vi-VN", { hour12: false });
  li.textContent = `[${timeStr}] ${message}`;
  li.className = `log-${type}`;
  logsEl.prepend(li);
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}
