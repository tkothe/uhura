import asyncio
import time

import pytest
from conftest import AUTH, BRIEF, OTHER, TOOL, make, make_settings, until

from uhura import phrases


async def draft(client, **changes):
    resp = await client.post("/calls", json={**BRIEF, **changes}, headers=AUTH)
    return resp.json()["id"]


async def status_is(client, call_id, status):
    async def check():
        call = (await client.get(f"/calls/{call_id}", headers=AUTH)).json()
        return call if call["status"] == status else None

    return await until(check)


def test_requires_token(run):
    async def go():
        client, _ = make()
        assert (await client.post("/calls", json=BRIEF)).status_code == 401
        assert (await client.get("/calls")).status_code == 401

    run(go())


def test_draft_does_not_dial_and_shows_brief(run):
    async def go():
        client, voice = make()
        resp = await client.post("/calls", json=BRIEF, headers=AUTH)
        body = resp.json()
        assert resp.status_code == 200 and body["status"] == "draft"
        assert body["to_number"] == "+493023125000"
        assert "KI-Assistent" in body["first_message"]
        assert body["message"] == phrases.DRAFTED
        assert voice.started == []

    run(go())


def test_guardrail_refusal(run):
    async def go():
        client, _ = make()
        resp = await client.post("/calls", json={**BRIEF, "to": "112"}, headers=AUTH)
        assert resp.status_code == 422 and "emergency" in resp.json()["detail"]

    run(go())


def test_unsupported_language_is_refused_at_draft(run):
    async def go():
        client, _ = make()
        resp = await client.post("/calls", json={**BRIEF, "language": "fr"}, headers=AUTH)
        assert resp.status_code == 422 and "fr" in resp.json()["detail"]

    run(go())


def test_users_only_see_their_own_calls(run):
    async def go():
        client, _ = make()
        call_id = await draft(client)
        other = OTHER
        assert (await client.get(f"/calls/{call_id}", headers=other)).status_code == 404
        assert (await client.get("/calls", headers=other)).json() == {"calls": []}
        mine = (await client.get("/calls", headers=AUTH)).json()["calls"]
        assert [c["id"] for c in mine] == [call_id]

    run(go())


def test_full_call_with_question_and_instruction(run):
    async def go():
        client, voice = make()
        call_id = await draft(client)

        resp = await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        assert resp.json()["message"] == phrases.OPEN
        assert voice.started[0]["to"] == "+493023125000"
        assert voice.started[0]["call_id"] == call_id

        # A second confirm must not dial again.
        assert (await client.post(f"/calls/{call_id}/confirm", headers=AUTH)).status_code == 409

        # The agent asks; a human answers through the events/answer endpoints.
        ask = asyncio.create_task(
            client.post("/agent-tools/ask_principal", json={"call_id": call_id, "question": "Which day?"}, headers=TOOL)
        )
        events = (await client.get(f"/calls/{call_id}/events?after=1&timeout=1", headers=AUTH)).json()["events"]
        question = next(e for e in events if e["type"] == "question")
        assert question["question"] == "Which day?"
        await client.post(f"/calls/{call_id}/instructions", json={"text": "Also ask about parking"}, headers=AUTH)
        resp = await client.post(
            f"/calls/{call_id}/answer", json={"question_id": question["seq"], "text": "Sunday"}, headers=AUTH
        )
        assert resp.json() == {"delivered": True}
        result = (await ask).json()
        assert result["answer"] == "Sunday" and result["answered"] is True
        assert result["new_instructions"] == ["Also ask about parking"]

        # Instructions are delivered once.
        check = await client.post("/agent-tools/final_check", json={"call_id": call_id}, headers=TOOL)
        assert check.json() == {"new_instructions": []}

        voice.finish()
        call = await status_is(client, call_id, "done")
        assert call["duration_secs"] == 42
        assert call["cost"]["credits"] == 900 and call["cost"]["billed_minutes"] == 0.7
        listed = (await client.get("/calls", headers=AUTH)).json()["calls"]
        assert listed[0]["credits"] == 900
        assert call["transcript"][0]["message"] == "Guten Tag"
        events = (await client.get(f"/calls/{call_id}/events?timeout=0", headers=AUTH)).json()["events"]
        assert events[-1]["type"] == "call_ended" and events[-1]["message"] == phrases.CLOSED

    run(go())


def test_unanswered_question_times_out_with_fallback(run):
    async def go():
        client, _ = make(ask_timeout=0.2)
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        resp = await client.post(
            "/agent-tools/ask_principal", json={"call_id": call_id, "question": "Budget?"}, headers=TOOL
        )
        body = resp.json()
        assert body["answered"] is False and "follow up by email" in body["answer"]
        # Answering after the agent gave up is reported, not silently dropped.
        late = await client.post(f"/calls/{call_id}/answer", json={"question_id": 2, "text": "x"}, headers=AUTH)
        assert late.status_code == 409

    run(go())


def test_an_answer_only_reaches_the_call_that_asked(run):
    async def go():
        client, _ = make()
        erikas = await draft(client)
        await client.post(f"/calls/{erikas}/confirm", headers=AUTH)
        ask = asyncio.create_task(
            client.post("/agent-tools/ask_principal", json={"call_id": erikas, "question": "Which day?"}, headers=TOOL)
        )
        question = await until(lambda: _question(client, erikas))

        # Another user, and another call of the same user, cannot answer it.
        other = OTHER
        maxs = (await client.post("/calls", json=BRIEF, headers=other)).json()["id"]
        resp = await client.post(f"/calls/{maxs}/answer", json={"question_id": question, "text": "x"}, headers=other)
        assert resp.status_code == 409
        second = await draft(client)
        resp = await client.post(f"/calls/{second}/answer", json={"question_id": question, "text": "x"}, headers=AUTH)
        assert resp.status_code == 409

        resp = await client.post(
            f"/calls/{erikas}/answer", json={"question_id": question, "text": "Friday"}, headers=AUTH
        )
        assert resp.status_code == 200
        assert (await ask).json()["answer"] == "Friday"

    run(go())


async def _question(client, call_id):
    events = (await client.get(f"/calls/{call_id}/events?timeout=0", headers=AUTH)).json()["events"]
    return next((e["seq"] for e in events if e["type"] == "question"), None)


def test_concurrent_confirms_dial_once(run):
    async def go():
        client, voice = make()
        voice.delay = 0.05
        call_id = await draft(client)
        responses = await asyncio.gather(*[client.post(f"/calls/{call_id}/confirm", headers=AUTH) for _ in range(3)])
        assert sorted(r.status_code for r in responses) == [200, 409, 409]
        assert len(voice.started) == 1
        # Nor can a rehearsal start while the call is being dialled or running.
        assert (await client.post(f"/calls/{call_id}/rehearsal", headers=AUTH)).status_code == 409
        assert voice.rehearsals == []

    run(go())


def test_concurrent_confirms_respect_the_daily_limit(run):
    async def go():
        client, voice = make(daily_calls=1)
        voice.delay = 0.05
        ids = [await draft(client) for _ in range(3)]
        responses = await asyncio.gather(*[client.post(f"/calls/{i}/confirm", headers=AUTH) for i in ids])
        assert sorted(r.status_code for r in responses) == [200, 409, 409]
        assert len(voice.started) == 1

    run(go())


def test_concurrent_rehearsal_starts_open_one_session(run):
    async def go():
        client, voice = make()
        voice.delay = 0.05
        call_id = await draft(client)
        starts = [client.post(f"/calls/{call_id}/rehearsal", headers=AUTH) for _ in range(2)]
        confirm = client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        *rehearsals, dialled = await asyncio.gather(*starts, confirm)
        # Exactly one of the three wins the draft; which one depends on scheduling.
        codes = [r.status_code for r in (*rehearsals, dialled)]
        assert sorted(codes) == [200, 409, 409]
        assert len(voice.rehearsals) + len(voice.started) == 1
        assert len(voice.started) == (dialled.status_code == 200)
        await client.delete(f"/calls/{call_id}/rehearsal", headers=AUTH)

    run(go())


def test_progress_reports_become_events_only_when_the_call_asked_for_them(run):
    async def go():
        client, _ = make()  # the service default is on
        reporting, silent = await draft(client), await draft(client, progress=False)
        for call_id in (reporting, silent):
            await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
            body = {"call_id": call_id, "stage": "hold", "note": "Warteschlange, Position 4"}
            resp = await client.post("/agent-tools/report_progress", json=body, headers=TOOL)
            assert resp.json()["recorded"] == (call_id == reporting)
            assert "do not send it again" in resp.json()["next"]

        events = (await client.get(f"/calls/{reporting}/events?timeout=0", headers=AUTH)).json()["events"]
        assert events[-1] == {**events[-1], "type": "progress", "stage": "hold", "note": "Warteschlange, Position 4"}
        events = (await client.get(f"/calls/{silent}/events?timeout=0", headers=AUTH)).json()["events"]
        assert "progress" not in [e["type"] for e in events]

        # The same report delivered twice in a row is stored once.
        again = {"call_id": reporting, "stage": "hold", "note": "Warteschlange, Position 4"}
        assert (await client.post("/agent-tools/report_progress", json=again, headers=TOOL)).json()["recorded"] is False

        bad = {"call_id": reporting, "stage": "whatever", "note": "x"}
        assert (await client.post("/agent-tools/report_progress", json=bad, headers=TOOL)).status_code == 422
        assert (await client.post("/agent-tools/report_progress", json=bad)).status_code == 401

        # The service default applies when the brief leaves it open, and is stored with the call.
        client2, _ = make(progress=False)
        call = (await client2.post("/calls", json=BRIEF, headers=AUTH)).json()
        assert call["brief"]["progress"] is False and "report_progress" not in call["prompt"]

    run(go())


def test_refused_consent_keeps_no_transcript(run):
    async def go():
        client, voice = make()
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        resp = await client.post("/agent-tools/consent_refused", json={"call_id": call_id}, headers=TOOL)
        assert resp.json()["recorded"] is True
        refused_at = client.manager.store.get_call(call_id)["consent_refused_at"]
        # A second report does not move the time of the refusal.
        await client.post("/agent-tools/consent_refused", json={"call_id": call_id}, headers=TOOL)
        assert client.manager.store.get_call(call_id)["consent_refused_at"] == refused_at
        events = (await client.get(f"/calls/{call_id}/events?timeout=0", headers=AUTH)).json()["events"]
        assert [e["message"] for e in events if e["type"] == "consent_refused"] == [phrases.CONSENT_REFUSED]

        voice.finish()
        call = await status_is(client, call_id, "done")
        # Only the fact remains: when, how long, what it cost. ElevenLabs' copy is deleted too.
        assert call["transcript"] is None and call["consent_refused_at"] == refused_at
        assert call["duration_secs"] == 42 and call["cost"]["credits"] == 900
        assert voice.deleted == ["conv_1"]

    run(go())


def test_refused_consent_drops_the_transcript_even_if_elevenlabs_keeps_it(run):
    async def go():
        client, voice = make()
        voice.delete_fails = True
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        await client.post("/agent-tools/consent_refused", json={"call_id": call_id}, headers=TOOL)
        voice.finish()
        call = await status_is(client, call_id, "done")
        assert call["transcript"] is None and voice.deleted == []

    run(go())


def test_consent_refused_in_a_rehearsal_does_not_mark_the_call(run):
    async def go():
        client, voice = make()
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/rehearsal", headers=AUTH)
        await client.post("/agent-tools/consent_refused", json={"call_id": call_id}, headers=TOOL)
        await client.delete(f"/calls/{call_id}/rehearsal", headers=AUTH)
        await status_is(client, call_id, "draft")
        # The real call afterwards keeps its transcript; the rehearsal only showed the tool.
        await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        voice.finish()
        call = await status_is(client, call_id, "done")
        assert call["transcript"] == [{"role": "agent", "message": "Guten Tag"}] and voice.deleted == []

    run(go())


def test_agent_tools_need_secret_and_a_live_call(run):
    async def go():
        client, _ = make()
        assert (await client.post("/agent-tools/final_check", json={"call_id": "x"})).status_code == 401
        call_id = await draft(client)  # a draft is not live
        resp = await client.post("/agent-tools/final_check", json={"call_id": call_id}, headers=TOOL)
        assert resp.status_code == 404
        resp = await client.post(f"/calls/{call_id}/instructions", json={"text": "x"}, headers=AUTH)
        assert resp.status_code == 409

    run(go())


def test_daily_limit_blocks_confirm(run):
    async def go():
        client, voice = make(daily_calls=1)
        first, second = await draft(client), await draft(client)
        assert (await client.post(f"/calls/{first}/confirm", headers=AUTH)).status_code == 200
        resp = await client.post(f"/calls/{second}/confirm", headers=AUTH)
        assert resp.status_code == 409 and "daily limit" in resp.json()["detail"]
        assert len(voice.started) == 1

    run(go())


def test_running_calls_count_at_full_length_against_the_monthly_budget(run):
    async def go():
        # 40 minutes leave room for two calls of up to 20 minutes while neither has a result.
        client, voice = make(monthly_minutes=40)
        ids = [await draft(client) for _ in range(3)]
        for call_id in ids[:2]:
            assert (await client.post(f"/calls/{call_id}/confirm", headers=AUTH)).status_code == 200
        resp = await client.post(f"/calls/{ids[2]}/confirm", headers=AUTH)
        assert resp.status_code == 409 and "monthly budget" in resp.json()["detail"]
        assert len(voice.started) == 2

    run(go())


def test_provider_failure_marks_call_failed(run):
    async def go():
        client, voice = make()
        voice.fail_with = RuntimeError("no phone number configured")
        call_id = await draft(client)
        resp = await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        assert resp.status_code == 502 and "no phone number" in resp.json()["detail"]
        call = (await client.get(f"/calls/{call_id}", headers=AUTH)).json()
        assert call["status"] == "failed" and "no phone number" in call["error"]
        # A call that never started does not use up the daily limit.
        assert client.manager.store.usage("erika")[0] == 0

    run(go())


def test_call_the_provider_never_finishes_is_given_up(run):
    async def go():
        client, _ = make(max_call_seconds=0)
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        call = await status_is(client, call_id, "failed")
        assert "no result from the provider" in call["error"]

        # The time it may have run is counted, not zero.
        manager = client.manager
        stuck = await draft(client)
        manager.store.update_call(stuck, status="in_progress", conversation_id="conv_2", started_at=time.time() - 120)
        await manager.startup()
        assert 120 <= (await status_is(client, stuck, "failed"))["duration_secs"] < 130
        await manager.shutdown()

    run(go())


def test_a_result_that_cannot_be_stored_still_ends_the_call(run):
    async def go():
        client, voice = make()
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/confirm", headers=AUTH)
        voice.error = {"unexpected": "shape"}  # not storable as it is
        voice.finish("failed")
        call = await status_is(client, call_id, "failed")
        assert "could not store the result" in call["error"]
        events = (await client.get(f"/calls/{call_id}/events?timeout=0", headers=AUTH)).json()["events"]
        assert events[-1]["type"] == "call_ended"

    run(go())


def test_restart_retires_calls_it_cannot_resume(run):
    async def go():
        client, voice = make()
        manager = client.manager
        orphan, unstarted = await draft(client), await draft(client)
        manager.store.update_call(orphan, status="in_progress")  # never reached ElevenLabs
        manager.store.update_call(unstarted, status="in_progress", conversation_id="conv_1")  # no started_at

        await manager.startup()
        assert manager.store.get_call(orphan)["status"] == "failed"
        voice.finish()
        await status_is(client, unstarted, "done")
        await manager.shutdown()

    run(go())


def test_rehearsal_runs_the_briefing_without_dialling(run):
    async def go():
        client, voice = make()
        call_id = await draft(client)
        started = (await client.post(f"/calls/{call_id}/rehearsal", headers=AUTH)).json()
        assert started["status"] == "rehearsing" and started["message"] == phrases.REHEARSAL_OPEN
        assert voice.started == []
        assert voice.rehearsals[0]["call_id"] == call_id
        assert "Ask for opening hours" in voice.rehearsals[0]["prompt"]

        voice.session.agent_says("Guten Tag")
        await client.post(f"/calls/{call_id}/rehearsal/say", json={"text": "Hallo"}, headers=AUTH)
        assert voice.session.heard == ["Hallo"]

        # The agent's tools work during a rehearsal, as in a real call.
        await client.post(f"/calls/{call_id}/instructions", json={"text": "Ask about parking"}, headers=AUTH)
        check = await client.post("/agent-tools/final_check", json={"call_id": call_id}, headers=TOOL)
        assert check.json() == {"new_instructions": ["Ask about parking"]}

        # A rehearsing call cannot be dialled or rehearsed twice.
        assert (await client.post(f"/calls/{call_id}/confirm", headers=AUTH)).status_code == 409
        assert (await client.post(f"/calls/{call_id}/rehearsal", headers=AUTH)).status_code == 409

        assert (await client.delete(f"/calls/{call_id}/rehearsal", headers=AUTH)).json()["stopped"] is True
        await status_is(client, call_id, "draft")
        after = started["events_after"]
        events = (await client.get(f"/calls/{call_id}/events?after={after}&timeout=0", headers=AUTH)).json()["events"]
        assert [e["type"] for e in events] == [
            "rehearsal_started",
            "agent_said",
            "callee_said",
            "instruction_queued",
            "rehearsal_ended",
        ]

        # Rehearsing used no call budget, and the draft can still be dialled.
        assert client.manager.store.usage("erika") == (0, 0)
        assert (await client.post(f"/calls/{call_id}/confirm", headers=AUTH)).status_code == 200

    run(go())


def test_rehearsals_are_limited_per_day(run):
    async def go():
        client, voice = make(daily_rehearsals=1)
        voice.delay = 0.05
        first, second = await draft(client), await draft(client)
        # Concurrent starts on different drafts count each other.
        responses = await asyncio.gather(*[client.post(f"/calls/{i}/rehearsal", headers=AUTH) for i in (first, second)])
        assert sorted(r.status_code for r in responses) == [200, 409]
        assert "daily limit of 1 rehearsals" in next(r for r in responses if r.status_code == 409).json()["detail"]
        assert len(voice.rehearsals) == 1
        for call_id in (first, second):
            await client.delete(f"/calls/{call_id}/rehearsal", headers=AUTH)

    run(go())


def test_rehearsal_is_closed_after_the_time_limit(run):
    async def go():
        client, voice = make(max_rehearsal_seconds=0.1)
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/rehearsal", headers=AUTH)
        await status_is(client, call_id, "draft")
        events = (await client.get(f"/calls/{call_id}/events?timeout=0", headers=AUTH)).json()["events"]
        assert "time limit" in events[-1]["reason"]

    run(go())


def test_rehearsal_that_cannot_start_leaves_the_draft(run):
    async def go():
        client, voice = make()
        voice.fail_with = RuntimeError("refused")
        call_id = await draft(client)
        assert (await client.post(f"/calls/{call_id}/rehearsal", headers=AUTH)).status_code == 502
        assert (await client.get(f"/calls/{call_id}", headers=AUTH)).json()["status"] == "draft"
        events = (await client.get(f"/calls/{call_id}/events?timeout=0", headers=AUTH)).json()["events"]
        assert [e["type"] for e in events] == ["rehearsal_started", "rehearsal_ended"]

    run(go())


def test_rehearsal_ends_when_the_agent_hangs_up(run):
    async def go():
        client, voice = make()
        call_id = await draft(client)
        await client.post(f"/calls/{call_id}/rehearsal", headers=AUTH)
        voice.session.hang_up()
        await status_is(client, call_id, "draft")
        resp = await client.post(f"/calls/{call_id}/rehearsal/say", json={"text": "Hallo?"}, headers=AUTH)
        assert resp.status_code == 409

    run(go())


def test_restart_resumes_running_calls_and_resets_rehearsals(run):
    async def go():
        client, voice = make()
        manager = client.manager
        running, rehearsing, dialling = await draft(client), await draft(client), await draft(client)
        manager.store.update_call(running, status="in_progress", conversation_id="conv_1", started_at=time.time())
        manager.store.update_call(rehearsing, status="rehearsing")
        manager.store.update_call(dialling, status="dialling", started_at=time.time())

        await manager.startup()
        assert manager.store.get_call(rehearsing)["status"] == "draft"
        # Interrupted while dialling: it may have gone through, so it is never dialled again.
        assert manager.store.get_call(dialling)["status"] == "failed"
        voice.finish()
        await status_is(client, running, "done")
        await manager.shutdown()

    run(go())


def test_old_calls_are_purged_but_running_ones_kept(run):
    async def go():
        client, _ = make(retention_days=30)
        manager = client.manager
        old, running, recent = await draft(client), await draft(client), await draft(client)
        long_ago = time.time() - 31 * 86400
        for call_id in (old, running):
            manager.store.update_call(call_id, created_at=long_ago)
        manager.store.update_call(running, status="in_progress")
        manager.store.add_event(old, "call_ended", {})

        assert manager.purge() == 1
        assert manager.store.get_call(old) is None
        assert manager.store.events_after(old, 0) == []
        assert manager.store.get_call(running) and manager.store.get_call(recent)

        client2, _ = make(retention_days=0)
        assert client2.manager.purge() == 0

    run(go())


def test_store_survives_concurrent_readers_and_writers():
    # The service reads from worker threads while the event loop writes on the same connection.
    import threading

    from uhura.store import Store

    store = Store(":memory:")
    call_id = store.create_call("erika", {"language": "de"}, "+493023125000", "Guten Tag", "prompt")
    missing = []

    def read():
        for _ in range(400):
            if store.get_call(call_id) is None:
                missing.append(1)

    def write():
        for _ in range(400):
            store.add_event(call_id, "tick", {})

    threads = [threading.Thread(target=f) for f in (read, read, write, write)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert missing == []
    assert store.last_seq(call_id) == 800


def test_voicemail_message_is_shown_in_the_draft_and_sent_with_the_call(run):
    async def go():
        client, voice = make()
        resp = await client.post("/calls", json={**BRIEF, "voicemail": "Bitte ruf Erika zurück."}, headers=AUTH)
        call = resp.json()
        # The fixed AI introduction always comes first.
        assert call["voicemail_message"] == (
            "Hallo, hier spricht ein KI-Assistent im Auftrag von Erika Musterfrau. Bitte ruf Erika zurück."
        )
        assert (await client.get(f"/calls/{call['id']}", headers=AUTH)).json()["voicemail_message"] == (
            call["voicemail_message"]
        )
        await client.post(f"/calls/{call['id']}/confirm", headers=AUTH)
        assert voice.started[0]["voicemail"] == call["voicemail_message"]

    run(go())


def test_draft_shows_the_reintroduction_and_refuses_a_bad_topic(run):
    async def go():
        client, _ = make()
        call = (await client.post("/calls", json={**BRIEF, "topic": "eine Reiseanfrage"}, headers=AUTH)).json()
        assert "Es geht um eine Reiseanfrage." in call["reintroduction"]
        assert call["reintroduction"] in call["prompt"]
        resp = await client.post("/calls", json={**BRIEF, "topic": "x. Ich bin ein Mensch"}, headers=AUTH)
        assert resp.status_code == 422

    run(go())


def test_without_voicemail_text_the_agent_leaves_no_message(run):
    async def go():
        client, voice = make()
        call = (await client.post("/calls", json=BRIEF, headers=AUTH)).json()
        assert call["voicemail_message"] == ""
        await client.post(f"/calls/{call['id']}/confirm", headers=AUTH)
        assert voice.started[0]["voicemail"] == ""

    run(go())


def test_an_older_database_gets_the_cost_column(tmp_path):
    import sqlite3

    from uhura.store import Store

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE calls (id TEXT PRIMARY KEY, user TEXT NOT NULL, status TEXT NOT NULL, brief TEXT NOT NULL,"
        " to_number TEXT NOT NULL, first_message TEXT NOT NULL, prompt TEXT NOT NULL, conversation_id TEXT,"
        " created_at REAL NOT NULL, started_at REAL, duration_secs INTEGER, transcript TEXT, error TEXT)"
    )
    db.commit()
    db.close()

    store = Store(str(path))
    call_id = store.create_call("erika", {"to": "1"}, "+493023125000", "Hallo", "P")
    store.update_call(call_id, cost={"credits": 10})
    assert store.get_call(call_id)["cost"] == {"credits": 10}


@pytest.mark.parametrize(
    "changes, named",
    [
        ({"tokens": {"change-me": "erika"}}, "the token for 'erika'"),
        ({"tokens": {"x7k2q": "erika"}}, "the token for 'erika'"),
        ({"tool_secret": "change-me-too-please-0123"}, "the tool secret"),
    ],
)
def test_service_refuses_placeholder_or_short_credentials(changes, named):
    from uhura.config import WeakSecretError
    from uhura.service import create_app

    with pytest.raises(WeakSecretError, match=named) as refused:
        create_app(make_settings(**changes))
    # The message names the credential, never its value.
    for value in (*changes.get("tokens", {}), changes.get("tool_secret", "")):
        if value:
            assert value not in str(refused.value)
