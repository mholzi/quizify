"""Dev-only signal lab: what would the room have done? (#1185, section 5).

``python scripts/dev_server.py --signals`` wires the real house consumers —
quizmaster voice, party lights, room sounds, the ``quizify_*`` bus events — to
a recording fake Home Assistant instead of a real one. Nothing is executed:
``tts.speak``, ``light.turn_on``, ``media_player.play_media`` and every bus
event are written to a timeline with a timestamp.

Open ``/quizify/dev/signals``: buttons on the left fire one moment (a reveal,
a reveal with a 3/5/7/10 streak, the countdown, the winner, the setup's probe,
or any raw WebSocket frame to every open phone, TV and host tab); the
timeline on the right shows what reached the room, so the voice against the
sting (P2) shows up as two entries in the same second. A real game played
against the dev server lands in the same timeline.

Never imported by the integration, never reachable without the flag, and it
never talks to a real Home Assistant.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from typing import Any

from aiohttp import web

_LOGGER = logging.getLogger("quizify.dev_server.signals")
_LAB_KEY = web.AppKey("quizify_signal_lab", object)

#: Options for the fake house: one speaker, one TTS engine, two lights and a
#: cue URL each, so every consumer counts as configured.
_FAKE_OPTIONS = {
    "house_events_enabled": True,
    "party_light_entities": ["light.lab_left", "light.lab_right"],
    "media_player_entity": "media_player.lab_speaker",
    "tts_entity": "tts.lab_voice",
    "sfx_correct_url": "http://lab/sfx/correct.mp3",
    "sfx_wrong_url": "http://lab/sfx/wrong.mp3",
    "sfx_streak_url": "http://lab/sfx/streak.mp3",
    "sfx_winner_url": "http://lab/sfx/winner.mp3",
}


class RecordingHass:
    """The three doors the house consumers use, recorded instead of opened."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.timeline: list[dict[str, Any]] = []
        self.tasks: set[asyncio.Task] = set()
        self.data: dict[str, Any] = {}
        self._listeners: dict[str, list] = {}
        self._states: dict[str, SimpleNamespace] = {}
        self.bus = SimpleNamespace(async_listen=self._listen, async_fire=self._fire)
        self.services = SimpleNamespace(async_call=self._call)
        self.states = SimpleNamespace(get=self._states.get)

    def _stamp(self, kind: str, name: str, data: dict[str, Any]) -> None:
        self.timeline.append({
            "t": round(time.monotonic() - self.started, 3),
            "kind": kind,
            "name": name,
            "data": data,
        })

    def _listen(self, event_type: str, listener):
        self._listeners.setdefault(event_type, []).append(listener)
        return lambda: self._listeners.get(event_type, []).remove(listener)

    def _fire(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self._stamp("event", event_type, dict(data or {}))
        event = SimpleNamespace(event_type=event_type, data=dict(data or {}))
        for listener in list(self._listeners.get(event_type, [])):
            listener(event)

    async def _call(self, domain, service, data, blocking=False):  # noqa: ARG002
        self._stamp("service", f"{domain}.{service}", dict(data or {}))
        if (domain, service) == ("tts", "speak"):
            # A rough speaker: busy for ~70 ms per character, like a voice.
            entity = data.get("media_player_entity_id")
            seconds = max(1.0, len(str(data.get("message", ""))) * 0.07)
            self._states[entity] = SimpleNamespace(state="playing", attributes={})

            async def _done() -> None:
                await asyncio.sleep(seconds)
                self._states[entity] = SimpleNamespace(state="idle", attributes={})

            self.async_create_task(_done())

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def aclose(self) -> None:
        """Cancel the light pulses, speaker timers and waits still running."""
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def add_arguments(parser: Any) -> None:
    parser.add_argument(
        "--signals",
        action="store_true",
        help="signal lab at /quizify/dev/signals: the house consumers run "
        "against a recording fake Home Assistant (#1185)",
    )


def _summary(game: Any, milestone: int) -> Any:
    from custom_components.quizify.game.questions import Answer, Question
    from custom_components.quizify.game.state import AnswerResult, RoundSummary

    question = Question(
        id="lab", question="Lab question?",
        answers=[Answer("Europäischer Aal", True), Answer("Wels", False)],
    )
    results = [
        AnswerResult("Lena", True, 48, milestone or 1, 90,
                     milestone_bonus=25 if milestone else 0,
                     milestone_streak=milestone),
        AnswerResult("Ben", False, 0, 0, 40),
    ]
    board = [SimpleNamespace(name="Lena", score=90), SimpleNamespace(name="Ben", score=40)]
    return RoundSummary(question, question.answers[0], "", results, board)


def _fire_moment(app: web.Application, moment: str) -> None:
    lab = app[_LAB_KEY]
    handler = lab.handler
    game = lab.game
    if moment == "question":
        handler.question_shown(_summary(game, 0).question, 3, 10, ["Europäischer Aal", "Wels"])
    elif moment == "countdown":
        handler.time_running_out(9.5)
        handler.time_running_out(4.5)
    elif moment.startswith("reveal"):
        streak = int(moment.split(":", 1)[1]) if ":" in moment else 0
        if streak:
            handler._streak_milestone("Lena", streak, 25, getattr(game, "round", None))
        handler.reveal(game, _summary(game, streak))
    elif moment == "winner":
        from custom_components.quizify.game_events import EVENT_WINNER_DECIDED

        lab.house.event_emitter._fire(EVENT_WINNER_DECIDED, {"winner_name": "Lena", "score": 90})
    elif moment == "probe":
        for consumer in (lab.house.tts_announcer, lab.house.party_lights, lab.house.sound_effects):
            consumer.probe_reveal()


async def _page(_request: web.Request) -> web.Response:
    return web.Response(text=_PAGE, content_type="text/html")


async def _timeline(request: web.Request) -> web.Response:
    return web.json_response(request.app[_LAB_KEY].hass.timeline)


async def _clear(request: web.Request) -> web.Response:
    lab = request.app[_LAB_KEY]
    lab.hass.timeline.clear()
    lab.hass.started = time.monotonic()
    return web.json_response({"ok": True})


async def _fire(request: web.Request) -> web.Response:
    body = await request.json()
    if "frame" in body:
        # A raw WebSocket frame to every open phone, TV and host tab.
        frame = body["frame"]
        if not isinstance(frame, dict) or "type" not in frame:
            return web.json_response({"ok": False, "error": "frame needs a type"}, status=400)
        await request.app[_LAB_KEY].handler._conn.broadcast(frame)
    else:
        _fire_moment(request.app, str(body.get("moment", "")))
    return web.json_response({"ok": True})


def install(app: web.Application, args: Any, ctx: Any) -> None:
    """Wire the recording house into the dev server. No-op without the flag."""
    if not getattr(args, "signals", False):
        return
    from custom_components.quizify.house import build_house_consumers

    hass = RecordingHass()
    house = build_house_consumers(hass, _FAKE_OPTIONS, ctx.game)  # type: ignore[arg-type]
    house.attach()
    handler = ctx.ws_handler
    handler.set_tts_announcer(house.tts_announcer)
    handler.set_event_emitter(house.event_emitter)
    handler.set_party_lights(house.party_lights)
    handler.set_sound_effects(house.sound_effects)
    # The voice is on in the lab; the setup's configure_tts can change it.
    house.tts_announcer.configure(
        enabled=True, announce_question=True, announce_reveal=True, announce_standings=True
    )
    app[_LAB_KEY] = SimpleNamespace(hass=hass, house=house, handler=handler, game=ctx.game)
    app.router.add_get("/quizify/dev/signals", _page)
    app.router.add_get("/quizify/dev/signals/timeline", _timeline)
    app.router.add_post("/quizify/dev/signals/fire", _fire)
    app.router.add_post("/quizify/dev/signals/clear", _clear)

    async def _cleanup(_app: web.Application) -> None:
        await hass.aclose()

    app.on_cleanup.append(_cleanup)
    _LOGGER.warning("signal lab on: /quizify/dev/signals (recording fake HA)")


_MOMENTS = [
    ("question", "Question"), ("countdown", "Countdown"), ("reveal", "Reveal"),
    ("reveal:3", "Reveal + streak 3"), ("reveal:5", "Reveal + streak 5"),
    ("reveal:7", "Reveal + streak 7"), ("reveal:10", "Reveal + streak 10"),
    ("winner", "Winner"), ("probe", "Setup probe"),
]
_FRAMES = {
    "Steal from me": {"type": "powerup_applied", "powerup_type": "steal",
                      "source_player": "Ben", "target_player": "Lena", "stolen_points": 20},
    "Freeze": {"type": "powerup_applied", "powerup_type": "freeze",
               "source_player": "Ben", "target_player": "Lena"},
    "Joker": {"type": "powerup_applied", "powerup_type": "joker", "source_player": "Ben"},
}

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signal lab</title>
<style>
body{font:14px system-ui,sans-serif;margin:0;display:flex;gap:16px;padding:16px;background:#FAF6EE;color:#2A2820}
aside{flex:0 0 240px;display:flex;flex-direction:column;gap:6px}
button{min-height:36px;text-align:left;padding:6px 10px;border:1px solid #E5DFCF;background:#fff;border-radius:8px;cursor:pointer}
main{flex:1;min-width:0}
table{width:100%;border-collapse:collapse}td{padding:4px 6px;border-bottom:1px solid #E5DFCF;vertical-align:top}
.event{color:#7FA897}.service{color:#E88A7F}code{white-space:pre-wrap;word-break:break-word}
</style></head><body>
<aside><strong>Room moments</strong>__MOMENTS__<strong>Frames to every tab</strong>__FRAMES__
<textarea id="raw" rows="4" placeholder='{"type": "..."}'></textarea><button id="send">Send raw frame</button>
<button id="clear">Clear timeline</button></aside>
<main><strong>What reached the room</strong> (fake HA, nothing executed)<table id="tl"></table></main>
<script>
function post(body){return fetch('/quizify/dev/signals/fire',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
document.querySelectorAll('[data-moment]').forEach(function(b){b.onclick=function(){post({moment:b.dataset.moment});};});
document.querySelectorAll('[data-frame]').forEach(function(b){b.onclick=function(){post({frame:JSON.parse(b.dataset.frame)});};});
document.getElementById('send').onclick=function(){try{post({frame:JSON.parse(document.getElementById('raw').value)});}catch(e){alert(e);}};
document.getElementById('clear').onclick=function(){fetch('/quizify/dev/signals/clear',{method:'POST'});};
function esc(s){return String(s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
setInterval(function(){fetch('/quizify/dev/signals/timeline').then(function(r){return r.json();}).then(function(rows){
document.getElementById('tl').innerHTML=rows.map(function(r){return '<tr><td>'+r.t.toFixed(2)+' s</td><td class="'+r.kind+'">'+esc(r.name)+'</td><td><code>'+esc(JSON.stringify(r.data))+'</code></td></tr>';}).join('');});},500);
</script></body></html>"""

_PAGE = _PAGE.replace(
    "__MOMENTS__",
    "".join(f'<button data-moment="{m}">{label}</button>' for m, label in _MOMENTS),
).replace(
    "__FRAMES__",
    "".join(
        "<button data-frame='" + json.dumps(f).replace("'", "&#39;") + f"'>{label}</button>"
        for label, f in _FRAMES.items()
    ),
)
