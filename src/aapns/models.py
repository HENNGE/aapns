import json
from typing import Dict, Any, Union, List

import attr


def str_list(instance: object, attr: attr.Attribute, value: Any) -> None:
    if not isinstance(value, list):
        raise TypeError("Must be list of strings")
    for arg in value:
        if not isinstance(arg, str):
            raise TypeError("Must be list of strings")


@attr.s
class Localized:
    key = attr.ib(validator=attr.validators.instance_of(str))
    args = attr.ib(default=None, validator=attr.validators.optional(str_list))


def maybe_localized(
    thing: Union[str, Localized], nonloc: str, lockey: str, locarg: str
) -> Dict[str, Union[str, List[str]]]:
    if isinstance(thing, Localized):
        attr.validate(thing)
        localized = {lockey: thing.key}
        if thing.args:
            localized[locarg] = thing.args
        return localized
    else:
        return {nonloc: thing}


@attr.s
class Alert:
    body = attr.ib(validator=attr.validators.instance_of((str, Localized)))
    title = attr.ib(
        default=None,
        validator=attr.validators.optional(
            attr.validators.instance_of((str, Localized))
        ),
    )
    action_loc_key = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    launch_image = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )

    def get_dict(self) -> Dict[str, Any]:
        attr.validate(self)
        alert = {}
        if self.title:
            alert.update(
                maybe_localized(self.title, "title", "title-loc-key", "title-loc-args")
            )
        alert.update(maybe_localized(self.body, "body", "loc-key", "loc-args"))
        if self.action_loc_key:
            alert["action-loc-key"] = self.action_loc_key
        if self.launch_image:
            alert["launch-image"] = self.launch_image
        return alert


@attr.s
class Notification:
    alert = attr.ib(validator=attr.validators.instance_of(Alert))
    badge = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(int)),
    )
    sound = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    content_available = attr.ib(
        default=False, validator=attr.validators.instance_of(bool)
    )
    category = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    thread_id = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    extra = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(dict)),
    )

    def get_dict(self) -> Dict[str, Any]:
        attr.validate(self)
        apns = {"alert": self.alert.get_dict()}
        if self.badge:
            apns["badge"] = self.badge
        if self.sound:
            apns["sound"] = self.sound
        if self.content_available:
            apns["content-available"] = 1
        if self.category:
            apns["category"] = self.category
        if self.thread_id:
            apns["thread-id"] = self.thread_id
        raw = {"aps": apns}
        if self.extra:
            raw.update(self.extra)
        return raw

    def encode(self) -> bytes:
        raw = self.get_dict()
        s = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return s.encode("utf-8")
