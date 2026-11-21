/**
 * A card that shows the map of a Xiaomi robot and lets the robot be
 * sent to a place that is picked on the map.
 *
 * The card talks to the services of the integration xiaomi_vacuum_map.
 * The place that is picked is given in the pixels of the picture of the
 * map, and the integration turns it into the millimetres of the house
 * with the same numbers the picture was drawn with.
 *
 * Three things can be done:
 *   - a click sends the robot to that place,
 *   - a drag cleans the block that was dragged over,
 *   - a room of the list cleans that room.
 */

const CARD_VERSION = "1.0.0";

class XiaomiVacuumMapCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("xiaomi-vacuum-map-card-editor");
  }

  static getStubConfig() {
    return {
      type: "custom:xiaomi-vacuum-map-card",
      entity: "camera.vacuum_map",
      title: "掃地機器人",
    };
  }

  static getCardSize() {
    return 6;
  }

  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._drag = null;
    this._pointer = null;
    this._message = "";
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("you need to give the entity of the map");
    }
    this._config = config;
    if (!this._root) {
      this._root = this.attachShadow({ mode: "open" });
      this._root.innerHTML = `
        <style>
          ha-card {
            padding: 0;
            overflow: hidden;
          }
          .wrap {
            position: relative;
            display: block;
            width: 100%;
            touch-action: none;
            user-select: none;
            cursor: crosshair;
          }
          img.map {
            display: block;
            width: 100%;
            height: auto;
            pointer-events: none;
          }
          .marker {
            position: absolute;
            width: 18px;
            height: 18px;
            margin-left: -9px;
            margin-top: -9px;
            border-radius: 50%;
            border: 2px solid #d32f2f;
            background: rgba(211, 47, 47, 0.35);
            pointer-events: none;
            display: none;
          }
          .sel {
            position: absolute;
            border: 2px solid #1565c0;
            background: rgba(21, 101, 192, 0.18);
            pointer-events: none;
            display: none;
          }
          .bar {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            padding: 8px 12px;
          }
          .bar .title {
            font-weight: 600;
            font-size: 1.1em;
            flex: 1 1 auto;
          }
          .rooms {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            padding: 0 12px 10px;
          }
          .rooms button {
            padding: 6px 10px;
            border-radius: 16px;
            border: 1px solid var(--divider-color, #ccc);
            background: var(--card-background-color, #fff);
            color: var(--primary-text-color, #222);
            font-size: 0.9em;
            cursor: pointer;
          }
          .rooms button:hover {
            border-color: var(--primary-color, #03a9f4);
          }
          .msg {
            padding: 0 12px 10px;
            font-size: 0.85em;
            color: var(--secondary-text-color, #666);
            min-height: 1.2em;
          }
          .msg.err {
            color: var(--error-color, #db4437);
          }
        </style>
        <ha-card>
          <div class="wrap">
            <img class="map" alt="地圖" />
            <div class="marker"></div>
            <div class="sel"></div>
          </div>
          <div class="bar">
            <span class="title"></span>
            <span class="state"></span>
          </div>
          <div class="rooms"></div>
          <div class="msg"></div>
        </ha-card>
      `;

      const wrap = this._root.querySelector(".wrap");
      wrap.addEventListener("pointerdown", (ev) => this._onDown(ev));
      wrap.addEventListener("pointermove", (ev) => this._onMove(ev));
      wrap.addEventListener("pointerup", (ev) => this._onUp(ev));
      wrap.addEventListener("pointercancel", () => this._clear());
    }
    this._root.querySelector(".title").textContent =
      this._config.title || "掃地機器人";
    this._renderRooms();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  get cardSize() {
    return 6;
  }

  /* --- the state of the map --- */

  get _state() {
    if (!this._hass) return null;
    return this._hass.states[this._config.entity];
  }

  get _geometry() {
    const state = this._state;
    if (!state) return null;
    return state.attributes["map_geometry"] || null;
  }

  get _rooms() {
    const state = this._state;
    if (!state) return [];
    return state.attributes["rooms"] || [];
  }

  _render() {
    const state = this._state;
    if (!state) return;

    const img = this._root.querySelector("img.map");
    const src = `/api/camera_proxy/${this._config.entity}?t=${Date.now()}`;
    if (img.getAttribute("data-src") !== src) {
      img.setAttribute("data-src", src);
      img.setAttribute("src", src);
    }

    const el = this._root.querySelector(".state");
    if (el) el.textContent = state.state === "idle" ? "" : state.state;

    // The rooms are only there when the state is there, so the list is
    // drawn again when the state changes.
    if (!this._roomsDrawn) {
      this._roomsDrawn = true;
      this._renderRooms();
    }
  }

  _renderRooms() {
    const box = this._root.querySelector(".rooms");
    if (!box) return;
    const rooms = this._rooms;
    const names = this._config.room_names || {};
    const wanted = this._config.rooms;

    box.innerHTML = "";
    for (const room of rooms) {
      if (wanted && !wanted.includes(room.room_id)) continue;
      const button = document.createElement("button");
      button.textContent = names[room.room_id] || `房間 ${room.room_id}`;
      button.addEventListener("click", () => this._cleanRoom(room.room_id));
      box.appendChild(button);
    }
  }

  /* --- the places that are picked --- */

  _pointOf(ev) {
    const wrap = this._root.querySelector(".wrap");
    const rect = wrap.getBoundingClientRect();
    const geometry = this._geometry;
    if (!geometry || !rect.width) return null;

    // The place in the picture of the map, not in the picture on the
    // screen. The map is drawn with a known number of pixels for every
    // cell, so the two are in step.
    const px = ((ev.clientX - rect.left) / rect.width) * geometry.image_width;
    const py = ((ev.clientY - rect.top) / rect.height) * geometry.image_height;
    return { px, py, rect };
  }

  _onDown(ev) {
    const point = this._pointOf(ev);
    if (!point) return;
    ev.preventDefault();
    this._drag = { start: point, end: point };
    this._pointer = ev.pointerId;
    if (this._root.querySelector(".wrap").setPointerCapture) {
      try {
        this._root.querySelector(".wrap").setPointerCapture(ev.pointerId);
      } catch (err) {
        /* the pointer is already gone */
      }
    }
  }

  _onMove(ev) {
    if (!this._drag) return;
    const point = this._pointOf(ev);
    if (!point) return;
    this._drag.end = point;
    this._showSelection();
  }

  _onUp(ev) {
    if (!this._drag) return;
    const drag = this._drag;
    this._drag = null;
    this._pointer = null;

    const dx = Math.abs(drag.end.px - drag.start.px);
    const dy = Math.abs(drag.end.py - drag.start.py);
    const geometry = this._geometry;
    const cell = geometry ? geometry.scale : 4;

    if (dx < cell && dy < cell) {
      // A click: the robot goes to that place.
      this._clear();
      this._goTo(drag.start.px, drag.start.py);
    } else {
      // A drag: the block that was dragged over is cleaned.
      this._cleanZone(drag.start.px, drag.start.py, drag.end.px, drag.end.py);
      this._clear();
    }
  }

  _showSelection() {
    if (!this._drag) return;
    const geometry = this._geometry;
    const sel = this._root.querySelector(".sel");
    const wrap = this._root.querySelector(".wrap");
    const rect = wrap.getBoundingClientRect();
    if (!geometry || !rect.width) return;

    const sx = (this._drag.start.px / geometry.image_width) * rect.width;
    const sy = (this._drag.start.py / geometry.image_height) * rect.height;
    const ex = (this._drag.end.px / geometry.image_width) * rect.width;
    const ey = (this._drag.end.py / geometry.image_height) * rect.height;

    sel.style.display = "block";
    sel.style.left = `${Math.min(sx, ex)}px`;
    sel.style.top = `${Math.min(sy, ey)}px`;
    sel.style.width = `${Math.abs(ex - sx)}px`;
    sel.style.height = `${Math.abs(ey - sy)}px`;
  }

  _clear() {
    const sel = this._root.querySelector(".sel");
    const marker = this._root.querySelector(".marker");
    if (sel) sel.style.display = "none";
    if (marker) marker.style.display = "none";
  }

  _say(text, isError) {
    const msg = this._root.querySelector(".msg");
    if (!msg) return;
    msg.textContent = text;
    msg.classList.toggle("err", Boolean(isError));
  }

  /* --- the commands --- */

  async _call(service, data) {
    this._say("送出指令…");
    try {
      await this._hass.callService("xiaomi_vacuum_map", service, {
        entity_id: this._config.entity,
        ...data,
      });
      this._say("已送出");
    } catch (err) {
      this._say(`失敗：${err && err.message ? err.message : err}`, true);
    }
  }

  _goTo(px, py) {
    const marker = this._root.querySelector(".marker");
    const geometry = this._geometry;
    const rect = this._root.querySelector(".wrap").getBoundingClientRect();
    if (marker && geometry) {
      marker.style.display = "block";
      marker.style.left = `${(px / geometry.image_width) * rect.width}px`;
      marker.style.top = `${(py / geometry.image_height) * rect.height}px`;
    }
    this._call("go_to_point", { x: px, y: py });
  }

  _cleanZone(x1, y1, x2, y2) {
    this._call("clean_zone", { x: x1, y: y1, x2: x2, y2: y2 });
  }

  _cleanRoom(roomId) {
    this._call("clean_room", { room_id: roomId });
  }
}

class XiaomiVacuumMapCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this.innerHTML = `
      <div class="row">
        <label for="entity">地圖實體</label>
        <input id="entity" value="${config.entity || ""}" />
      </div>
      <div class="row">
        <label for="title">標題</label>
        <input id="title" value="${config.title || ""}" />
      </div>
    `;
    this.querySelector("#entity").addEventListener("change", (ev) => {
      this._config.entity = ev.target.value;
      this._fire();
    });
    this.querySelector("#title").addEventListener("change", (ev) => {
      this._config.title = ev.target.value;
      this._fire();
    });
  }

  _fire() {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      })
    );
  }
}

if (!customElements.get("xiaomi-vacuum-map-card")) {
  customElements.define("xiaomi-vacuum-map-card", XiaomiVacuumMapCard);
}
if (!customElements.get("xiaomi-vacuum-map-card-editor")) {
  customElements.define(
    "xiaomi-vacuum-map-card-editor",
    XiaomiVacuumMapCardEditor
  );
}

// Tell the version of the card to the console, so that a problem with
// an old version in the cache is easy to see.
console.info(`%c XIAOMI VACUUM MAP CARD %c ${CARD_VERSION} `,
  "background:#03a9f4;color:#fff;font-weight:700",
  "background:#222;color:#fff");

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "xiaomi-vacuum-map-card")) {
  window.customCards.push({
    type: "xiaomi-vacuum-map-card",
    name: "Xiaomi Vacuum Map Card",
    description: "掃地機器人地圖，可點擊地圖指定位置",
    preview: true,
  });
}
