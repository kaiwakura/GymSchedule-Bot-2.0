// GymScheduleBot 2.0 front end: your own events, AI Coach chat (text + screenshots), toolbar.
// Plain JavaScript, no build step.

const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
const STORAGE_KEY = "gymschedulebot.events.v1";
const toolbar = document.getElementById("toolbar");
const planRegion = document.getElementById("plan-region");
const messages = document.getElementById("messages");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendButton = chatForm.querySelector(".send");

document.querySelectorAll(".tz-input").forEach((el) => (el.value = browserTz));

// ------------------------------------------------- your events (localStorage) ---
// Your own events live in this browser, so they survive server restarts. The server
// validates them on every request; this is just storage.
function loadEvents() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
function saveEvents(events) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(events));
  } catch {
    /* storage full or blocked: events still work for this page view */
  }
}
let myEvents = loadEvents();

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, events: myEvents, timezone: browserTz }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  if (data.html) {
    planRegion.innerHTML = data.html;
    revealWorkouts();
  }
  if (Array.isArray(data.events)) {
    myEvents = data.events; // the server's cleaned list (including events the coach added)
    saveEvents(myEvents);
  }
  return data;
}
const replan = () => post("/api/plan", {});

// If the calendar is taller than the screen, scroll it so the first workout is in view.
function revealWorkouts() {
  const wrap = planRegion.querySelector(".week-wrap");
  const workouts = [...planRegion.querySelectorAll(".workout")];
  if (!wrap || !workouts.length || wrap.scrollHeight <= wrap.clientHeight) return;
  const header = planRegion.querySelector(".cal-head")?.offsetHeight || 0;
  const firstTop = Math.min(...workouts.map((w) => w.offsetTop));
  const lastBottom = Math.max(...workouts.map((w) => w.offsetTop + w.offsetHeight));
  if (lastBottom + header > wrap.scrollTop + wrap.clientHeight) {
    wrap.scrollTop = Math.max(0, firstTop - 3 * 40);
  }
}
revealWorkouts();

// Forms that need your events (Download .ics, Add to Google Calendar).
planRegion.addEventListener("submit", (e) => {
  const input = e.target.querySelector(".events-input");
  if (input) input.value = JSON.stringify(myEvents);
});

// ------------------------------------------------------------- toolbar ---
toolbar.querySelectorAll("select, input[type='radio'], .more input[type='number']").forEach((el) =>
  el.addEventListener("change", () => toolbar.requestSubmit())
);

// ------------------------------------------------------------- event editor ---
const dialog = document.getElementById("event-dialog");
const eventForm = document.getElementById("event-form");
const daySelect = document.getElementById("event-day");
const deleteBtn = document.getElementById("event-delete");
const errorBox = document.getElementById("event-error");
let editingId = null;

function fillDays(selectedDate) {
  daySelect.innerHTML = "";
  planRegion.querySelectorAll(".day-col[data-date]").forEach((col) => {
    const opt = document.createElement("option");
    opt.value = `${col.dataset.date}|${col.dataset.weekday}`;
    opt.textContent = col.dataset.label;
    if (col.dataset.date === selectedDate) opt.selected = true;
    daySelect.appendChild(opt);
  });
}

const pad = (n) => String(n).padStart(2, "0");
const toHHMM = (minutes) => `${pad(Math.floor(minutes / 60) % 24)}:${pad(minutes % 60)}`;

function openEditor({ date, eventId, startMinutes }) {
  errorBox.hidden = true;
  editingId = eventId || null;
  const ev = myEvents.find((x) => x.id === editingId);
  document.getElementById("event-dialog-title").textContent = ev ? "Edit event" : "Add an event";
  deleteBtn.hidden = !ev;
  eventForm.title.value = ev ? ev.title : "";
  const startAt = startMinutes ?? 9 * 60;
  eventForm.start.value = ev ? ev.start : toHHMM(startAt);
  eventForm.end.value = ev ? ev.end : toHHMM(Math.min(startAt + 60, 23 * 60 + 59));
  eventForm.weekly.checked = ev ? ev.repeat === "weekly" : true;
  let dateForSelect = date;
  if (ev && ev.repeat === "once") dateForSelect = ev.date;
  if (ev && ev.repeat === "weekly") {
    const match = [...planRegion.querySelectorAll(".day-col[data-weekday]")].find(
      (b) => Number(b.dataset.weekday) === ev.weekday
    );
    dateForSelect = match ? match.dataset.date : date;
  }
  fillDays(dateForSelect);
  dialog.showModal();
  eventForm.title.focus();
}

eventForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = eventForm.title.value.trim();
  const start = eventForm.start.value;
  const end = eventForm.end.value;
  if (!title || !start || !end || start >= end) {
    errorBox.textContent = "Add a title and make sure it ends after it starts.";
    errorBox.hidden = false;
    return;
  }
  const [date, weekday] = daySelect.value.split("|");
  const event = { id: editingId || `e_${Math.random().toString(16).slice(2, 10)}`, title, start, end };
  if (eventForm.weekly.checked) Object.assign(event, { repeat: "weekly", weekday: Number(weekday) });
  else Object.assign(event, { repeat: "once", date });
  myEvents = editingId ? myEvents.map((x) => (x.id === editingId ? event : x)) : [...myEvents, event];
  saveEvents(myEvents);
  dialog.close();
  await replan();
});
deleteBtn.addEventListener("click", async () => {
  myEvents = myEvents.filter((x) => x.id !== editingId);
  saveEvents(myEvents);
  dialog.close();
  await replan();
});
document.getElementById("event-cancel").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (e) => {
  if (e.target === dialog) dialog.close();
});

// ------------------------------------------------------------------ chat ---
function addMessage(text, who, imageUrl) {
  const row = document.createElement("div");
  row.className = `msg ${who === "user" ? "user-msg" : "coach-msg"}`;
  if (who !== "user") {
    const bot = document.createElement("span");
    bot.className = "mini-bot";
    row.appendChild(bot);
  }
  const bubble = document.createElement("p");
  if (imageUrl) {
    const img = document.createElement("img");
    img.src = imageUrl;
    img.alt = "Schedule screenshot";
    img.className = "msg-image";
    bubble.appendChild(img);
  }
  if (text) bubble.appendChild(document.createTextNode(text)); // text node, never innerHTML
  row.appendChild(bubble);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

// Screenshot attachments
const imageInput = document.getElementById("image-input");
const attachment = document.getElementById("attachment");
const attachmentThumb = document.getElementById("attachment-thumb");
let pendingImage = null; // { data: dataURL, type }

function shrinkImage(file) {
  // Resize to at most 2000px so uploads stay small but text stays readable.
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, 2000 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve({ data: canvas.toDataURL("image/jpeg", 0.9), type: "image/jpeg" });
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => reject(new Error("That file isn't an image I can read."));
    img.src = URL.createObjectURL(file);
  });
}

imageInput.addEventListener("change", async () => {
  const file = imageInput.files[0];
  imageInput.value = "";
  if (!file) return;
  try {
    pendingImage = await shrinkImage(file);
    attachmentThumb.src = pendingImage.data;
    document.getElementById("attachment-name").textContent = file.name;
    attachment.hidden = false;
    chatInput.placeholder = "Add a note (optional) and press send";
    chatInput.focus();
  } catch (err) {
    addMessage(err.message, "coach");
  }
});
document.getElementById("attachment-remove").addEventListener("click", () => {
  pendingImage = null;
  attachment.hidden = true;
  chatInput.placeholder = "Ask to change your plan…";
});

async function send(text) {
  text = text.trim();
  const image = pendingImage;
  if ((!text && !image) || sendButton.disabled) return;
  addMessage(text, "user", image && image.data);
  chatInput.value = "";
  autoGrow();
  pendingImage = null;
  attachment.hidden = true;
  chatInput.placeholder = "Ask to change your plan…";
  sendButton.disabled = true;
  const typing = addMessage(image ? "Reading your schedule" : "Thinking", "coach");
  typing.classList.add("typing");
  try {
    const data = await post("/api/chat", image ? { message: text, image } : { message: text });
    typing.remove();
    addMessage(data.reply, "coach");
  } catch (err) {
    typing.remove();
    addMessage(`Sorry, ${err.message.charAt(0).toLowerCase()}${err.message.slice(1)} Please try again.`, "coach");
  } finally {
    sendButton.disabled = false;
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  send(chatInput.value);
});
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send(chatInput.value);
  }
});
function autoGrow() {
  chatInput.style.height = "auto";
  chatInput.style.height = `${chatInput.scrollHeight}px`;
}
chatInput.addEventListener("input", autoGrow);
document.getElementById("chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip && !chip.dataset.action) send(chip.textContent);
});

// ------------------------------------------------- buttons anywhere on the page ---
document.addEventListener("click", async (e) => {
  const target = e.target.closest("[data-action], [data-dialog], [data-event-id]");
  if (!target) return;
  const action = target.dataset.action;

  if (target.dataset.dialog) {
    document.getElementById(target.dataset.dialog)?.showModal();
  } else if (target.dataset.eventId) {
    openEditor({ eventId: target.dataset.eventId });
  } else if (action === "add-event") {
    openEditor({ date: target.dataset.date });
  } else if (action === "screenshot") {
    imageInput.click();
  } else if (action === "clear-events") {
    if (myEvents.length && confirmClear()) {
      myEvents = [];
      saveEvents(myEvents);
      await replan();
    }
  } else if (action === "replan" && target.getAttribute("aria-busy") !== "true") {
    target.setAttribute("aria-busy", "true");
    const typing = addMessage("Re-planning your week", "coach");
    typing.classList.add("typing");
    try {
      const data = await post("/api/chat", { action: "replan" });
      typing.remove();
      addMessage(data.reply, "coach");
    } catch {
      typing.remove();
      addMessage("Sorry, I couldn't re-plan right now. Please try again.", "coach");
    }
  }
});

// Click an empty spot in a day column to add an event at that time (snapped to 30 minutes).
planRegion.addEventListener("click", (e) => {
  const col = e.target.closest(".day-col");
  if (!col || e.target !== col) return; // clicks on events and workouts are handled above
  const rect = col.getBoundingClientRect();
  const fraction = (e.clientY - rect.top) / rect.height;
  const minutes = Number(col.dataset.firstHour) * 60 + fraction * Number(col.dataset.hours) * 60;
  openEditor({ date: col.dataset.date, startMinutes: Math.floor(minutes / 30) * 30 });
});

function confirmClear() {
  return window.confirm(`Delete all ${myEvents.length} of your events? This can't be undone.`);
}
// Close a workout dialog by clicking its backdrop.
planRegion.addEventListener("click", (e) => {
  if (e.target.tagName === "DIALOG") e.target.close();
});

// ------------------------------------------------------------------ start ---
// The server rendered the page without your events (they live here), so re-plan with them.
if (myEvents.length || document.body.dataset.tz !== browserTz) {
  planRegion.classList.add("loading");
  replan()
    .then((data) => {
      const intro = document.getElementById("coach-intro");
      if (intro && data.intro) intro.textContent = data.intro;
    })
    .catch(() => {})
    .finally(() => planRegion.classList.remove("loading"));
}
