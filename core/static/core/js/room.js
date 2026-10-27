// MediConnect — teleconsultation room: chat (polling) + peer-to-peer video (WebRTC).
// The server only relays messages; the video goes directly between the two browsers.
// Waiting room (patient): poll until the doctor enters, then reload into the consultation.
(() => {
  const lobby = document.querySelector("[data-lobby]");
  if (!lobby) return;
  const status = lobby.querySelector("[data-lobby-status]");
  const check = async () => {
    try {
      const res = await fetch(lobby.dataset.chatUrl, { headers: { Accept: "application/json" } });
      if (res.ok) {
        const data = await res.json();
        if (data.status !== "scheduled") {
          status.textContent = "Le médecin arrive, ouverture de la consultation…";
          window.location.reload();
          return;
        }
      } else if (res.status === 404) {
        status.textContent = "Ce rendez-vous n'est plus disponible.";
        return;
      }
    } catch (e) { /* network hiccup: retry */ }
    setTimeout(check, 2000);
  };
  check();
})();

(() => {
  const root = document.querySelector("[data-room]");
  if (!root) return;

  const role = root.dataset.role;
  const chatUrl = root.dataset.chatUrl;
  const signalUrl = root.dataset.signalUrl;
  const csrf = root.dataset.csrf;
  const $ = (sel) => document.querySelector(sel);

  const CHAT_EVERY = 2500;
  const SIGNAL_EVERY = 1500;

  // ------------------------------------------------------------------
  // Chat
  // ------------------------------------------------------------------
  const list = $("[data-messages]");
  const empty = $("[data-chat-empty]");
  const form = $("[data-chat-form]");
  const textarea = form.querySelector("textarea");
  const presence = $("[data-presence]");
  const presenceLabel = $("[data-presence-label]");
  let lastId = 0;
  let closed = false;

  function renderMessage(m) {
    if (m.id <= lastId) return;
    lastId = m.id;
    if (empty) empty.remove();
    const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    let node;
    if (m.kind === "system") {
      node = document.createElement("div");
      node.className = "msg-system";
      node.textContent = `${m.body} · ${m.time}`;
    } else {
      node = document.createElement("div");
      node.className = m.mine ? "msg mine" : "msg";
      const meta = document.createElement("span");
      meta.className = "msg-meta";
      meta.textContent = m.mine ? `Vous · ${m.time}` : `${m.sender} · ${m.time}`;
      const bubble = document.createElement("div");
      bubble.className = "msg-bubble";
      if (m.kind === "document") renderDocument(bubble, m);
      else bubble.textContent = m.body; // textContent: no HTML injection
      node.append(meta, bubble);
    }
    list.appendChild(node);
    if (nearBottom || m.mine) list.scrollTop = list.scrollHeight;
  }

  function link(href, text, icon) {
    const a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    if (icon) {
      const i = document.createElement("i");
      i.className = `bi bi-${icon}`;
      a.append(i, " ");
    }
    a.append(text);
    return a;
  }

  // A shared document: title, type, links (the doctor also gets the AI report)
  function renderDocument(bubble, m) {
    const doc = m.document;
    bubble.classList.add("msg-doc");
    const icon = document.createElement("i");
    icon.className = "bi bi-file-earmark-medical";
    const text = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = doc ? doc.title : m.body;
    const info = document.createElement("span");
    info.textContent = doc ? `${doc.kind} · ${doc.info}` : "Document supprimé";
    text.append(title, info);
    bubble.append(icon, text);
    if (!doc) return;
    const actions = document.createElement("div");
    actions.className = "msg-doc-actions";
    actions.append(link(doc.url, "Ouvrir"));
    if (doc.analysis_api) {
      const ai = document.createElement("button");
      ai.type = "button";
      ai.className = "msg-doc-ai";
      ai.innerHTML = '<i class="bi bi-robot"></i> Lire avec l\'IA';
      ai.addEventListener("click", () => openAiReading(doc));
      actions.append(ai);
    }
    bubble.append(actions);
  }

  // ------------------------------------------------------------------
  // "Lire avec l'IA" (doctor): the agent reads the document, the report
  // is shown in a window inside the room.
  // ------------------------------------------------------------------
  const aiDialog = $("[data-ai-dialog]");
  const aiBody = $("[data-ai-body]");
  let aiCurrent = null; // document being shown
  let aiTimer = null;

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function aiSection(title, items, className) {
    const section = el("section", null, `ai-section ${className || ""}`);
    section.append(el("h4", title));
    const ul = el("ul");
    items.forEach((item) => ul.append(typeof item === "string" ? el("li", item) : item));
    section.append(ul);
    return section;
  }

  function renderAi(data) {
    aiBody.replaceChildren();
    $("[data-ai-rerun]").hidden = !["done", "error"].includes(data.status);
    const full = $("[data-ai-full]");
    full.hidden = data.status !== "done";
    if (data.report_url) full.href = data.report_url;

    if (data.status === "running" || data.status === "none") {
      const wait = el("div", null, "ai-wait");
      wait.append(el("span", null, "spinner spinner-dark"), el("strong", "L'IA lit le document…"),
        el("p", "Le compte rendu s'affiche ici dans quelques secondes. Vous pouvez continuer la consultation."));
      aiBody.append(wait);
      return;
    }
    if (data.status === "error") {
      aiBody.append(el("div", `L'analyse n'a pas pu aboutir. ${data.error || ""} Relancez-la ou lisez le document directement.`, "alert alert-danger"));
      return;
    }
    const meta = [data.document_type, data.exam_date && `examen du ${data.exam_date}`].filter(Boolean).join(" · ");
    if (meta) aiBody.append(el("p", meta, "ai-meta"));
    if (!data.readable) aiBody.append(el("div", "Document illisible ou non médical.", "alert alert-danger"));
    aiBody.append(el("p", data.summary, "ai-summary"));

    if (data.abnormal_values.length) {
      const items = data.abnormal_values.map((v) => {
        const li = el("li");
        const arrow = v.flag === "high" ? "↑" : v.flag === "low" ? "↓" : "!";
        li.append(el("strong", `${arrow} ${v.name} : ${v.value} ${v.unit}`.trim()));
        if (v.reference) li.append(el("span", ` (réf. ${v.reference})`, "text-muted"));
        return li;
      });
      aiBody.append(aiSection(`Valeurs hors normes (${data.abnormal_values.length} sur ${data.values_count})`, items, "ai-abnormal"));
    } else if (data.values_count) {
      aiBody.append(el("p", `Les ${data.values_count} valeurs relevées sont dans les normes.`, "ai-meta"));
    }
    if (data.abnormal_findings.length) aiBody.append(aiSection("Anomalies relevées", data.abnormal_findings));
    if (data.points_to_check.length) aiBody.append(aiSection("Points à vérifier", data.points_to_check));
  }

  async function loadAi(method, extra) {
    if (!aiCurrent) return;
    const doc = aiCurrent;
    clearTimeout(aiTimer);
    try {
      const options = method === "POST"
        ? { method, headers: { "X-CSRFToken": csrf }, body: new URLSearchParams(extra || {}) }
        : { headers: { Accept: "application/json" } };
      const res = await fetch(doc.analysis_api, options);
      if (aiCurrent !== doc) return; // another document was opened meanwhile
      const data = await res.json();
      renderAi(data);
      if (data.status === "running" && aiDialog.open) aiTimer = setTimeout(() => loadAi("GET"), 3000);
    } catch (err) {
      renderAi({ status: "error", error: "Connexion perdue." });
    }
  }

  function openAiReading(doc) {
    if (!aiDialog) return;
    aiCurrent = doc;
    $("[data-ai-doc]").textContent = doc.title;
    renderAi({ status: "running" });
    aiDialog.showModal();
    loadAi("POST"); // starts the reading if it was never done
  }

  $("[data-ai-close]")?.addEventListener("click", () => aiDialog.close());
  aiDialog?.addEventListener("close", () => { clearTimeout(aiTimer); aiCurrent = null; });
  aiDialog?.addEventListener("click", (e) => { if (e.target === aiDialog) aiDialog.close(); }); // click outside
  $("[data-ai-rerun]")?.addEventListener("click", () => {
    renderAi({ status: "running" });
    loadAi("POST", { relancer: "1" });
  });

  // Attach a document (lab results…) from the chat
  const attachInput = $("[data-attach]");
  const attachLabel = $("[data-attach-label]");
  attachInput?.addEventListener("change", async () => {
    const file = attachInput.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      alert("Le fichier dépasse 5 Mo.");
      attachInput.value = "";
      return;
    }
    const data = new FormData();
    data.append("file", file);
    attachLabel.classList.add("is-loading");
    try {
      const res = await fetch(root.dataset.shareUrl, { method: "POST", headers: { "X-CSRFToken": csrf }, body: data });
      const json = await res.json();
      if (res.ok) {
        renderMessage(json.message);
        applyState(json);
      } else {
        alert(json.error || "Document refusé.");
      }
    } catch (err) {
      alert("Connexion perdue : document non envoyé.");
    }
    attachLabel.classList.remove("is-loading");
    attachInput.value = "";
  });

  function applyState(state) {
    const online = Boolean(state.other_online);
    presence.classList.toggle("online", online);
    presenceLabel.textContent = online ? "En ligne" : "Hors ligne";
    if (state.status === "closed" && !closed) {
      closed = true;
      textarea.disabled = true;
      form.querySelector("button[type=submit]").disabled = true;
      if (attachInput) attachInput.disabled = true;
      hangUp(false);
      $("[data-room-closed]").hidden = false;
      const badge = $("[data-room-badge]");
      if (badge) { badge.textContent = "Terminée"; badge.className = "badge room-status room-closed"; }
    }
  }

  async function pollChat() {
    try {
      const res = await fetch(`${chatUrl}?after=${lastId}`, { headers: { Accept: "application/json" } });
      if (res.ok) {
        const data = await res.json();
        data.messages.forEach(renderMessage);
        applyState(data);
      }
    } catch (e) { /* network hiccup: retry on next tick */ }
    if (!closed) setTimeout(pollChat, CHAT_EVERY);
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = textarea.value.trim();
    if (!body) return;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const res = await fetch(chatUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrf, "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ body }),
      });
      const data = await res.json();
      if (res.ok) {
        textarea.value = "";
        renderMessage(data.message);
        applyState(data);
      } else {
        alert(data.error || "Message non envoyé.");
      }
    } catch (err) {
      alert("Connexion perdue : message non envoyé.");
    }
    button.disabled = closed;
    textarea.focus();
  });

  // Enter sends, Shift+Enter adds a new line
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  // ------------------------------------------------------------------
  // Timer
  // ------------------------------------------------------------------
  const timer = $("[data-timer]");
  const openedAt = timer && timer.dataset.opened ? new Date(timer.dataset.opened) : null;
  if (timer && openedAt && !isNaN(openedAt)) {
    const tick = () => {
      const s = Math.max(0, Math.floor((Date.now() - openedAt.getTime()) / 1000));
      const mm = String(Math.floor(s / 60)).padStart(2, "0");
      const ss = String(s % 60).padStart(2, "0");
      timer.lastChild.textContent = ` ${mm}:${ss}`;
    };
    tick();
    setInterval(tick, 1000);
  }

  // ------------------------------------------------------------------
  // Video (WebRTC). The doctor always creates the offer.
  //   hello  -> "my camera is on"   (patient answers hello, doctor sends an offer)
  //   offer / answer / candidate    -> standard WebRTC negotiation
  //   bye    -> the other side hung up
  // ------------------------------------------------------------------
  const localVideo = $("[data-local-video]");
  const remoteVideo = $("[data-remote-video]");
  const placeholder = $("[data-remote-placeholder]");
  const remoteStatus = $("[data-remote-status]");
  const startBtn = $("[data-start-video]");
  const micBtn = $("[data-toggle-mic]");
  const camBtn = $("[data-toggle-cam]");
  const hangBtn = $("[data-hangup]");
  const errorBox = $("[data-video-error]");
  const other = root.dataset.other;

  const rtcConfig = { iceServers: [{ urls: "stun:stun.l.google.com:19302" }] };
  let pc = null;
  let localStream = null;
  let active = false;
  let pendingCandidates = [];
  let signalTimer = null;

  function showError(text) {
    errorBox.textContent = text;
    errorBox.hidden = !text;
  }

  // Browsers may refuse to autoplay a video with sound. In that case play it
  // muted (always allowed) and offer a button to turn the sound on.
  const unmuteBtn = $("[data-unmute]");
  async function playRemote() {
    try {
      await remoteVideo.play();
      if (!remoteVideo.muted && unmuteBtn) unmuteBtn.hidden = true;
    } catch (err) {
      remoteVideo.muted = true;
      try { await remoteVideo.play(); } catch (e) { /* still blocked */ }
      if (unmuteBtn) unmuteBtn.hidden = false;
    }
  }
  unmuteBtn?.addEventListener("click", () => {
    remoteVideo.muted = false;
    remoteVideo.play().catch(() => {});
    if (unmuteBtn) unmuteBtn.hidden = true;
  });

  function setRemoteVisible(visible, text) {
    placeholder.hidden = visible;
    remoteVideo.classList.toggle("is-live", visible);
    if (text) remoteStatus.textContent = text;
  }

  async function sendSignal(type, data) {
    try {
      await fetch(signalUrl, {
        method: "POST",
        headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
        body: JSON.stringify({ type, data }),
      });
    } catch (e) { /* ignored: the other side will retry with hello */ }
  }

  // Each negotiation has a number (chosen by the doctor's offer). Answers and
  // candidates from an older attempt are ignored, so two "hello" crossing each
  // other can't leave the call half-connected.
  let callId = 0;

  function closePeer() {
    if (pc) {
      pc.ontrack = pc.onicecandidate = pc.onconnectionstatechange = null;
      pc.close();
    }
    pc = null;
    remoteVideo.srcObject = null;
    if (unmuteBtn) unmuteBtn.hidden = true;
  }

  function createPeer(id) {
    closePeer();
    callId = id;
    pendingCandidates = pendingCandidates.filter((c) => c.id >= id);
    pc = new RTCPeerConnection(rtcConfig);
    localStream.getTracks().forEach((t) => pc.addTrack(t, localStream));
    pc.ontrack = (e) => {
      if (remoteVideo.srcObject !== e.streams[0]) remoteVideo.srcObject = e.streams[0];
      setRemoteVisible(true);
      playRemote();
    };
    pc.onicecandidate = (e) => {
      if (e.candidate) sendSignal("candidate", { id, candidate: e.candidate.toJSON() });
    };
    pc.onconnectionstatechange = () => {
      if (!pc) return;
      if (pc.connectionState === "connected" && remoteVideo.srcObject) {
        setRemoteVisible(true);
      } else if (pc.connectionState === "failed") {
        setRemoteVisible(false, "La connexion vidéo a échoué. Le chat reste disponible.");
      } else if (pc.connectionState === "disconnected") {
        setRemoteVisible(false, `Connexion avec ${other} interrompue…`);
      }
    };
  }

  async function flushCandidates() {
    const ready = pendingCandidates.filter((c) => c.id === callId);
    pendingCandidates = pendingCandidates.filter((c) => c.id > callId);
    for (const c of ready) {
      try { await pc.addIceCandidate(c.candidate); } catch (e) { /* stale candidate */ }
    }
  }

  async function handleSignal(msg) {
    if (!active) return;
    const data = msg.data || {};
    try {
      switch (msg.type) {
        case "hello":
          if (role === "doctor") {
            // Use a fresh, increasing id (time-based so a reloaded page still goes up).
            const id = Math.max(callId + 1, Date.now());
            setRemoteVisible(false, `Connexion avec ${other}…`);
            createPeer(id);
            const offer = await pc.createOffer();
            // Send the offer before applying it: ICE candidates only start
            // after setLocalDescription, so they always arrive after the offer.
            await sendSignal("offer", { id, type: offer.type, sdp: offer.sdp });
            await pc.setLocalDescription(offer);
          } else {
            sendSignal("hello");
          }
          break;
        case "offer": {
          if (data.id < callId) break; // older attempt
          setRemoteVisible(false, `Connexion avec ${other}…`);
          createPeer(data.id);
          await pc.setRemoteDescription({ type: data.type, sdp: data.sdp });
          await flushCandidates();
          const answer = await pc.createAnswer();
          await sendSignal("answer", { id: data.id, type: answer.type, sdp: answer.sdp });
          await pc.setLocalDescription(answer);
          break;
        }
        case "answer":
          if (pc && data.id === callId && pc.signalingState === "have-local-offer") {
            await pc.setRemoteDescription({ type: data.type, sdp: data.sdp });
            await flushCandidates();
          }
          break;
        case "candidate":
          if (pc && data.id === callId && pc.remoteDescription) {
            try { await pc.addIceCandidate(data.candidate); } catch (e) { /* stale */ }
          } else if (data.id >= callId) {
            pendingCandidates.push(data);
          }
          break;
        case "bye":
          closePeer();
          setRemoteVisible(false, `${other} a coupé sa caméra.`);
          break;
      }
    } catch (err) {
      console.warn("Signal error", err);
    }
  }

  async function pollSignals() {
    if (!active) return;
    try {
      const res = await fetch(signalUrl, { headers: { Accept: "application/json" } });
      if (res.ok) {
        const data = await res.json();
        for (const s of data.signals) await handleSignal(s);
      }
    } catch (e) { /* retry */ }
    if (active) signalTimer = setTimeout(pollSignals, SIGNAL_EVERY);
  }

  async function startVideo() {
    showError("");
    if (!navigator.mediaDevices || !window.RTCPeerConnection) {
      showError("Votre navigateur ne permet pas la vidéo ici (ouvrez le site en https ou sur localhost). Le chat reste disponible.");
      return;
    }
    startBtn.disabled = true;
    try {
      localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    } catch (err) {
      startBtn.disabled = false;
      showError("Impossible d'accéder à la caméra ou au micro. Vérifiez les autorisations du navigateur. Le chat reste disponible.");
      return;
    }
    localVideo.srcObject = localStream;
    localVideo.hidden = false;
    active = true;
    startBtn.hidden = true;
    [micBtn, camBtn, hangBtn].forEach((b) => (b.hidden = false));
    setRemoteVisible(false, `En attente de ${other}…`);
    await sendSignal("hello");
    pollSignals();
  }

  function hangUp(notify = true) {
    if (!active) return;
    active = false;
    clearTimeout(signalTimer);
    if (notify) sendSignal("bye");
    closePeer();
    if (localStream) localStream.getTracks().forEach((t) => t.stop());
    localStream = null;
    localVideo.srcObject = null;
    localVideo.hidden = true;
    startBtn.hidden = false;
    startBtn.disabled = closed;
    [micBtn, camBtn, hangBtn].forEach((b) => (b.hidden = true));
    setRemoteVisible(false, "Appel vidéo terminé. Le chat reste disponible.");
  }

  function toggleTrack(kind, btn, onIcon, offIcon, onLabel, offLabel) {
    if (!localStream) return;
    const tracks = kind === "audio" ? localStream.getAudioTracks() : localStream.getVideoTracks();
    const enabled = !tracks.every((t) => t.enabled);
    tracks.forEach((t) => (t.enabled = enabled));
    btn.classList.toggle("is-off", !enabled);
    btn.querySelector("i").className = `bi bi-${enabled ? onIcon : offIcon}`;
    btn.setAttribute("aria-label", enabled ? onLabel : offLabel);
  }

  startBtn.addEventListener("click", startVideo);
  hangBtn.addEventListener("click", () => hangUp(true));
  micBtn.addEventListener("click", () =>
    toggleTrack("audio", micBtn, "mic", "mic-mute", "Couper le micro", "Activer le micro"));
  camBtn.addEventListener("click", () =>
    toggleTrack("video", camBtn, "camera-video", "camera-video-off", "Couper la caméra", "Activer la caméra"));
  window.addEventListener("beforeunload", () => {
    // Best effort "bye" so the other side sees we left.
    if (active) {
      fetch(signalUrl, {
        method: "POST", keepalive: true,
        headers: { "X-CSRFToken": csrf, "Content-Type": "application/json" },
        body: JSON.stringify({ type: "bye" }),
      });
    }
  });

  // Small state snapshot, handy when debugging a call from the browser console.
  root.debugState = () => ({
    active,
    pending: pendingCandidates.length,
    pc: pc && { connection: pc.connectionState, ice: pc.iceConnectionState, signaling: pc.signalingState },
  });

  pollChat();
  root.dataset.ready = "1";
})();
