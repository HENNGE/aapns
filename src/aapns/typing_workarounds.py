"""
Workarounds for https://github.com/python-attrs/attrs/issues/576
"""
from typing import *

import attr

if TYPE_CHECKING:
    _T1 = TypeVar("_T1")
    _T2 = TypeVar("_T2")

    def instance_of_validator(
        t: Tuple[Type[_T1], Type[_T2]]
    ) -> attr._ValidatorType[Union[_T1, _T2]]:
        return cast(
            attr._ValidatorType[Union[_T1, _T2]], attr.validators.instance_of(t)
        )

    def optional_str_list_validator() -> attr._ValidatorType[Optional[List[str]]]:
        return cast(
            attr._ValidatorType[Optional[List[str]]],
            attr.validators.optional(
                attr.validators.deep_iterable(
                    attr.validators.instance_of(str), attr.validators.instance_of(list)
                )
            ),
        )

    def optional_str_dict_validator() -> attr._ValidatorType[Optional[Dict[str, Any]]]:
        return cast(
            attr._ValidatorType[Optional[Dict[str, Any]]],
            attr.validators.optional(
                attr.validators.deep_mapping(
                    attr.validators.instance_of(str),
                    attr.validators.instance_of(object),
                    attr.validators.instance_of(dict),
                )
            ),
        )


else:
    instance_of_validator = attr.validators.instance_of
    optional_str_list_validator = lambda: attr.validators.optional(
        attr.validators.deep_iterable(
            attr.validators.instance_of(str), attr.validators.instance_of(list)
        )
    )
    optional_str_dict_validator = lambda: attr.validators.optional(
        attr.validators.deep_mapping(
            attr.validators.instance_of(str),
            attr.validators.instance_of(object),
            attr.validators.instance_of(dict),
        )
    )
