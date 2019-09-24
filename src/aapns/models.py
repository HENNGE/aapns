import json
from typing import *

import attr

from .typing_workarounds import (
    instance_of_validator,
    optional_str_dict_validator,
    optional_str_list_validator,
)


@attr.s
class Localized:
    key: str = attr.ib(validator=attr.validators.instance_of(str))
    args: Optional[List[str]] = attr.ib(
        default=None, validator=optional_str_list_validator()
    )


MaybeLocalized = Union[Dict[str, str], Dict[str, Union[List[str], str]]]


def maybe_localized(
    thing: Union[str, Localized], nonloc: str, lockey: str, locarg: str
) -> MaybeLocalized:
    if isinstance(thing, Localized):
        attr.validate(thing)
        localized: Dict[str, Union[str, List[str]]] = {lockey: thing.key}
        if thing.args:
            localized[locarg] = thing.args
        return localized
    else:
        return {nonloc: thing}


@attr.s
class Alert:
    body: Union[str, Localized] = attr.ib(
        validator=instance_of_validator(str, Localized)
    )
    title: Optional[Union[str, Localized]] = attr.ib(
        default=None,
        validator=attr.validators.optional(instance_of_validator(str, Localized)),
    )
    action_loc_key: Optional[str] = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    launch_image: Optional[str] = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )

    def get_dict(self) -> Dict[str, Any]:
        attr.validate(self)
        alert: Dict[str, Any] = {}
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
    alert: Alert = attr.ib(validator=attr.validators.instance_of(Alert))
    badge: Optional[int] = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(int)),
    )
    sound: Optional[str] = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    content_available: bool = attr.ib(
        default=False, validator=attr.validators.instance_of(bool)
    )
    category: Optional[str] = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    thread_id: Optional[str] = attr.ib(
        default=None,
        validator=attr.validators.optional(attr.validators.instance_of(str)),
    )
    extra: Optional[Dict[str, Any]] = attr.ib(
        default=None, validator=optional_str_dict_validator()
    )

    def get_dict(self) -> Dict[str, Any]:
        attr.validate(self)
        apns: Dict[str, Any] = {"alert": self.alert.get_dict()}
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
