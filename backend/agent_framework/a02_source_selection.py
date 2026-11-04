"""Resolve model-selected PDF line IDs to exact, server-owned source excerpts.

This is source selection, never fuzzy quote repair or a semantic approval.
"""
from dataclasses import dataclass
from copy import deepcopy
import json
import re
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field

from .contracts import PDFTextSource
from .ports import AdapterFailure

MAX_SOURCE_LINES=2048
MAX_SOURCE_PARTS=8192
MAX_SELECTION_SCHEMA_BYTES=2*1024*1024
UNRESOLVED_SOURCE=re.compile(r'확인\s*중|미확정|회신\s*후')


class ValueSpan(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    start:str=Field(min_length=1,max_length=220)
    end:str=Field(min_length=1,max_length=220)


class SourceChoice(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    start_line:str=Field(min_length=1,max_length=200)
    end_line:str=Field(min_length=1,max_length=200)
    kind:Literal['text','number','visual','unsupported']
    operator:Literal['equals','contains','gte','lte','present','manual']
    expected:ValueSpan | None=Field(description='Exact value selected by source part IDs; null for manual or unresolved requirements.')
    required:bool
    unit:ValueSpan | None=Field(description='Measurement unit selected by source part IDs, or JSON null when unnecessary.')
    unresolved:bool


class SourceChoices(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    candidates:list[SourceChoice]=Field(min_length=1,max_length=128)


@dataclass(frozen=True)
class SourceLine:
    source:PDFTextSource
    start:int
    end:int


@dataclass(frozen=True)
class SourceIndex:
    lines:dict[str,SourceLine]
    parts:dict[str,SourceLine]


@dataclass(frozen=True)
class SelectedProviderReply:
    payload:bytes
    response_sha256:str


def invalid_source():
    raise AdapterFailure('A02_SOURCE_MISMATCH','The selected PDF source range is invalid.')


def index_sources(sources):
    index={};parts={};payload=[];blocks=set()
    for source in sources:
        if source.block_id in blocks:invalid_source()
        blocks.add(source.block_id)
        lines=[];offset=0
        for number,raw in enumerate(source.text.splitlines(keepends=True),1):
            # Remove only this line's delimiter. Spaces, Unicode and embedded
            # delimiters in a multi-line selection remain the original bytes.
            text=raw.removesuffix('\n').removesuffix('\r')
            if text.strip():
                identifier=f'{source.block_id}:L{number}'
                if identifier in index:invalid_source()
                index[identifier]=SourceLine(source,offset,offset+len(text))
                line_parts=[]
                # Split numbers from adjacent units without normalizing either.
                # Selecting multiple parts copies intervening spaces verbatim.
                for n,match in enumerate(re.finditer(r'\d+(?:[.,]\d+)*|[^\W\d_]+|[^\w\s]|_',text),1):
                    key=f'{identifier}:T{n}'
                    parts[key]=SourceLine(source,offset+match.start(),offset+match.end())
                    line_parts.append({'id':key,'text':match.group()})
                    if len(parts)>MAX_SOURCE_PARTS:
                        raise AdapterFailure('PDF_SOURCE_INDEX_LIMIT_EXCEEDED','The PDF has too many source parts for this extraction profile.')
                lines.append({'id':identifier,'text':text,'parts':line_parts})
                if len(index)>MAX_SOURCE_LINES:
                    raise AdapterFailure('PDF_SOURCE_INDEX_LIMIT_EXCEEDED','The PDF has too many source lines for this extraction profile.')
            offset+=len(raw)
        payload.append({'block_id':source.block_id,'page':source.page,'lines':lines})
    if not index:invalid_source()
    return SourceIndex(index,parts),payload


def selection_schema(index):
    schema=SourceChoices.model_json_schema()
    template=schema['$defs'].pop('SourceChoice')
    schema['$defs'].pop('ValueSpan')
    alternatives=[];size=0
    for ordinal,(key,first) in enumerate(index.lines.items()):
        ends=[end for end,last in index.lines.items()
            if last.source.block_id==first.source.block_id and last.source.page==first.source.page
            and last.start>=first.start and last.end-first.start<=4000]
        if not ends:
            raise AdapterFailure('PDF_SOURCE_INDEX_LIMIT_EXCEEDED','A PDF source line exceeds this extraction profile.')
        parts=[p for p in index.parts if p.startswith(key+':T')]
        # Bind the permitted value spans to the chosen first source line. The
        # grammar forbids other lines/pages and reversed value endpoints before
        # generation; the server rechecks these bounds independently afterward.
        spans=[];numeric=[];units=[]
        for n,start in enumerate(parts):
            span={'type':'object','additionalProperties':False,'required':['start','end'],
                'properties':{'start':{'type':'string','const':start},'end':{'type':'string','enum':parts[n:]}}}
            size+=len(json.dumps(span))
            if size>MAX_SELECTION_SCHEMA_BYTES:
                raise AdapterFailure('PDF_SOURCE_INDEX_LIMIT_EXCEEDED','The PDF source selection schema exceeds this extraction profile.')
            spans.append(span)
            part=index.parts[start];literal=part.source.text[part.start:part.end]
            if re.fullmatch(r'\d+(?:\.\d+)?',literal):
                numeric.append({'type':'object','additionalProperties':False,'required':['start','end'],
                    'properties':{'start':{'type':'string','const':start},'end':{'type':'string','const':start}}})
            elif literal in ('+','-') and n+1<len(parts):
                last=index.parts[parts[n+1]]
                if re.fullmatch(r'[+-]\d+(?:\.\d+)?',part.source.text[part.start:last.end]):
                    numeric.append({'type':'object','additionalProperties':False,'required':['start','end'],
                        'properties':{'start':{'type':'string','const':start},'end':{'type':'string','const':parts[n+1]}}})
            if re.fullmatch(r'[^\W\d_]+|[%℃°]',literal):
                units.append({'type':'object','additionalProperties':False,'required':['start','end'],
                    'properties':{'start':{'type':'string','const':start},'end':{'type':'string','const':start}}})
        name=f'LineValue{ordinal}'
        schema['$defs'][name]={'anyOf':spans} if spans else {'type':'null'}
        resolved_ends=[end for end in ends if not UNRESOLVED_SOURCE.search(first.source.text[first.start:index.lines[end].end])]
        def variant(kind,operator,expected,unit,unresolved,allowed_ends):
            choice=deepcopy(template);properties=choice['properties']
            properties.update(start_line={'type':'string','const':key},end_line={'type':'string','enum':allowed_ends},
                kind=kind,operator=operator,expected=expected,unit=unit,unresolved={'type':'boolean','const':unresolved})
            return choice
        null={'type':'null'};value={'$ref':f'#/$defs/{name}'}
        choices=[variant({'enum':['text','number','visual','unsupported']},{'const':'manual'},null,null,True,ends)]
        if resolved_ends:
            choices.append(variant({'enum':['text','number','visual','unsupported']},{'const':'manual'},null,null,False,resolved_ends))
            if spans:
                choices.append(variant({'const':'text'},{'enum':['equals','contains']},value,null,False,resolved_ends))
                choices.append(variant({'const':'visual'},{'const':'present'},value,null,False,resolved_ends))
            if numeric:
                number_name=f'LineNumber{ordinal}';schema['$defs'][number_name]={'anyOf':numeric}
                unit_schema={'anyOf':[null,*units]} if units else null
                choices.append(variant({'const':'number'},{'enum':['equals','gte','lte']},
                    {'$ref':f'#/$defs/{number_name}'},unit_schema,False,resolved_ends))
        size+=sum(len(json.dumps(choice)) for choice in choices)+len(json.dumps(numeric))
        if size>MAX_SELECTION_SCHEMA_BYTES:
            raise AdapterFailure('PDF_SOURCE_INDEX_LIMIT_EXCEEDED','The PDF source selection schema exceeds this extraction profile.')
        alternatives.extend(choices)
    schema['properties']['candidates']['items']={'anyOf':alternatives}
    return schema


def selected_value(span,index,first,last,code):
    if span is None:return None
    start=index.parts.get(span.start);end=index.parts.get(span.end)
    if (start is None or end is None or start.source.block_id!=first.source.block_id
            or end.source.block_id!=first.source.block_id or start.source.page!=first.source.page
            or end.source.page!=first.source.page or start.start>end.start
            or start.start<first.start or end.end>first.end):
        raise AdapterFailure(code,'The selected value must belong to its own PDF excerpt.')
    return first.source.text[start.start:end.end]


def validate_selected_comparison(choice,quote,expected,unit):
    # Do not assume the remote server actually enforced its supplied schema.
    if (choice.unresolved or UNRESOLVED_SOURCE.search(quote)) and expected is not None:
        raise AdapterFailure('A02_UNRESOLVED_VALUE_INVENTED','An unresolved requirement cannot contain a selected expected value.')
    valid=False
    if choice.operator=='manual':valid=expected is None and unit is None
    elif not choice.unresolved:
        if choice.kind=='text' and choice.operator in ('equals','contains'):
            valid=expected is not None and unit is None
        elif choice.kind=='visual' and choice.operator=='present':
            valid=expected is not None and unit is None
        elif choice.kind=='number' and choice.operator in ('equals','gte','lte'):
            valid=bool(expected is not None and re.fullmatch(r'[+-]?\d+(?:\.\d+)?',expected)
                and (unit is None or re.fullmatch(r'[^\W\d_]+|[%℃°]',unit)))
    if not valid:
        raise AdapterFailure('A02_RESPONSE_INVALID','The selected comparison does not match its kind or unresolved state.')


def materialize_selection(value,index):
    reply=SourceChoices.model_validate(value)
    candidates=[]
    for choice in reply.candidates:
        first=index.lines.get(choice.start_line);last=index.lines.get(choice.end_line)
        if (first is None or last is None or first.source.block_id!=last.source.block_id
                or first.source.page!=last.source.page or first.start>last.start):invalid_source()
        quote=first.source.text[first.start:last.end]
        if not quote.strip() or len(quote)>4000:invalid_source()
        expected=selected_value(choice.expected,index,first,last,'A02_EXPECTED_UNGROUNDED')
        unit=selected_value(choice.unit,index,first,last,'A02_UNIT_UNGROUNDED')
        validate_selected_comparison(choice,quote,expected,unit)
        candidates.append({**choice.model_dump(exclude={'start_line','end_line','expected','unit'}),
            'expected':'' if expected is None else expected,'unit':unit,
            'text':quote,'quote':quote,'block_id':first.source.block_id,'page':first.source.page})
    return json.dumps({'candidates':candidates},ensure_ascii=False).encode('utf-8')
