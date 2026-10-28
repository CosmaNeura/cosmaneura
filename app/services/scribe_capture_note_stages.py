"""Select each capture source channel once before composing its original envelope."""

import asyncio
import json

from app.services.scribe_capture_deadline import capture_call


def _closed(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _object(raw, key, max_bytes):
    def pairs(items):
        value = {}
        for name, content in items:
            if name in value:
                raise ValueError("duplicate_capture_stage_property")
            value[name] = content
        return value

    if not isinstance(raw, str) or len(raw.encode("utf-8")) > max_bytes:
        raise ValueError("capture_stage_unconfirmed")
    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict) or set(value) != {key}:
        raise ValueError("capture_stage_unconfirmed")
    return value[key]


class CaptureNoteStages:
    """Separate tasks using the same sources, preferences and configured model.

    Model output is never input to another stage. Each stage selects against the
    complete original sources, and the existing resolvers validate its channel.
    No stage publishes; capture_note retains its source/auth/persistence fences.
    """

    def __init__(
        self, *, response_schema, plan, history, model_factory, max_bytes, checkpoint
    ):
        self.schema = response_schema
        self.plan = plan
        self.history = history
        self.model_factory = model_factory
        self.max_bytes = max_bytes
        self.checkpoint = checkpoint

    async def complete(self, prompt, *, json_output):
        if not json_output:
            raise ValueError("capture_stage_json_required")
        # Keep the existing complete model phase deadline, not a new per-stage
        # overall allowance. SDK timeouts and zero retries remain unchanged.
        async with asyncio.timeout(35):
            return await self._complete(prompt)

    async def _stage(self, prompt, key, shape, instructions):
        # Each new request must still be authorized against the current source.
        # The existing callback closes its DB transaction before provider I/O.
        await self.checkpoint()
        raw = await capture_call(
            lambda: self.model_factory(response_schema=_closed({key: shape})).complete(
                prompt + instructions,
                json_output=True,
            )
        )
        return _object(raw, key, self.max_bytes)

    async def _complete(self, prompt):
        original_fields = self.schema["properties"]["fields"]["properties"]
        fields = dict.fromkeys(original_fields, "")
        result = {"fields": fields}
        if self.plan.fields:
            key = "plan_source_refs"
            result[key] = await self._stage(
                prompt,
                key,
                self.schema["properties"][key],
                self.plan.prompt()
                + "\nThis call performs only prospective-plan source selection. "
                "Do not compose narrative note fields or perform history selection. "
                "The original note schema and source preferences remain the selection "
                "guidance. For this call, override earlier output shapes and return "
                "exactly plan_source_refs with its existing source-reference object; "
                "do not return fields or other properties. Preserve the existing "
                "selection duties, exclusions, original roles and source uncertainty.\n",
            )
            # A malformed/out-of-bounds first selection cannot admit another
            # provider stage. This is the original resolver, not a text filter.
            self.plan.resolve(json.dumps(result), max_bytes=self.max_bytes)
        if self.history.fields:
            key = "reported_history_source_refs"
            result[key] = await self._stage(
                prompt,
                key,
                self.schema["properties"][key],
                self.history.prompt(has_plans=False)
                + "\nThis call performs only reported-history context selection. "
                "Do not compose narrative note fields or perform Plan selection. "
                "The original note schema and source preferences remain the selection "
                "guidance. For this call, override earlier output shapes and return "
                "exactly reported_history_source_refs with its existing "
                "source-reference object; do not return fields or other properties. "
                "Preserve the existing source-selection duties, exclusions, original "
                "roles and source uncertainty. Prospective-only care instructions "
                "belong in the separate Plan stage; retain complete mixed sources "
                "when they also contain needed history or clinical information.\n",
            )
            self.history.resolve(json.dumps(result), max_bytes=self.max_bytes)
        narrative = {
            name: shape
            for name, shape in original_fields.items()
            if shape.get("enum") != [""]
        }
        if narrative:
            generated = await self._stage(
                prompt,
                "fields",
                _closed(narrative),
                "\nThis call composes only these original narrative note fields: "
                + json.dumps(list(narrative))
                + ". Use their original schema instructions, source roles and saved "
                "preferences. The other sections are selected directly from the "
                "original sources in separate stages. Do not generate those sections "
                "or repeat their source context here. For this call, override earlier "
                "output shapes and return exactly fields with every listed field and "
                "no other fields or properties. Do not select Plan or history refs.\n",
            )
            if (
                not isinstance(generated, dict)
                or set(generated) != set(narrative)
                or any(not isinstance(value, str) for value in generated.values())
            ):
                raise ValueError("capture_stage_unconfirmed")
            fields.update(generated)
        # Preserve the original strict combined envelope and field order for the
        # unchanged source resolvers and final complete-note validator.
        raw = json.dumps(result, ensure_ascii=False)
        if len(raw.encode("utf-8")) > self.max_bytes:
            raise ValueError("capture_stage_unconfirmed")
        return raw
