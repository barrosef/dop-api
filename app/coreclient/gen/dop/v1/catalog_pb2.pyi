from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Plan(_message.Message):
    __slots__ = ("key", "name", "tagline", "features", "sort")
    KEY_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    TAGLINE_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    SORT_FIELD_NUMBER: _ClassVar[int]
    key: str
    name: str
    tagline: str
    features: _containers.RepeatedScalarFieldContainer[str]
    sort: int
    def __init__(self, key: _Optional[str] = ..., name: _Optional[str] = ..., tagline: _Optional[str] = ..., features: _Optional[_Iterable[str]] = ..., sort: _Optional[int] = ...) -> None: ...

class Provider(_message.Message):
    __slots__ = ("key", "category", "name", "credential_kind", "permissions", "needs_base_url", "operated", "docs_url", "brand_color", "sort")
    KEY_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CREDENTIAL_KIND_FIELD_NUMBER: _ClassVar[int]
    PERMISSIONS_FIELD_NUMBER: _ClassVar[int]
    NEEDS_BASE_URL_FIELD_NUMBER: _ClassVar[int]
    OPERATED_FIELD_NUMBER: _ClassVar[int]
    DOCS_URL_FIELD_NUMBER: _ClassVar[int]
    BRAND_COLOR_FIELD_NUMBER: _ClassVar[int]
    SORT_FIELD_NUMBER: _ClassVar[int]
    key: str
    category: str
    name: str
    credential_kind: str
    permissions: _containers.RepeatedScalarFieldContainer[str]
    needs_base_url: bool
    operated: bool
    docs_url: str
    brand_color: str
    sort: int
    def __init__(self, key: _Optional[str] = ..., category: _Optional[str] = ..., name: _Optional[str] = ..., credential_kind: _Optional[str] = ..., permissions: _Optional[_Iterable[str]] = ..., needs_base_url: _Optional[bool] = ..., operated: _Optional[bool] = ..., docs_url: _Optional[str] = ..., brand_color: _Optional[str] = ..., sort: _Optional[int] = ...) -> None: ...

class ListPlansRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListPlansResponse(_message.Message):
    __slots__ = ("plans",)
    PLANS_FIELD_NUMBER: _ClassVar[int]
    plans: _containers.RepeatedCompositeFieldContainer[Plan]
    def __init__(self, plans: _Optional[_Iterable[_Union[Plan, _Mapping]]] = ...) -> None: ...

class ListProvidersRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ListProvidersResponse(_message.Message):
    __slots__ = ("providers",)
    PROVIDERS_FIELD_NUMBER: _ClassVar[int]
    providers: _containers.RepeatedCompositeFieldContainer[Provider]
    def __init__(self, providers: _Optional[_Iterable[_Union[Provider, _Mapping]]] = ...) -> None: ...
