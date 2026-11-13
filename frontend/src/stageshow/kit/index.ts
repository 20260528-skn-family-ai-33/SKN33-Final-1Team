// Stageshow kit (MIT, © 2026 Benji Taylor) — 이 프로젝트에 필요한 부분만 옮겼다.
// 원본: https://github.com/benjitaylor/skills (stageshow/kit, 299140f). 범위와 제외 항목은 ../README.md 참고.
// Components import their own CSS, so no separate style import is required.

// The stage: the canvas, the script, the cursor, measuring and playback
export { Stage } from './stage'
export { useLook } from './looks'
export { useReducedMotion } from './reduced-motion'
export { inset, box, between, contains } from './measure'
export type { StageProps, Play, LookTokens } from './stage'
export type { Act, Script } from './act'
export type { Point, Rect, Target } from './measure'
export type { CursorKind, CursorStyle } from './cursor'
export type { Theme, Palette, Fidelity, Layout } from './looks'
export type { KeysAt } from './keys'
export { clock, installVirtualClock } from './clock'
export type { ClockControl } from './clock'

// Frames: window chrome and placeholder atoms
export { Window, Bar, Text, Prompt, Caret } from './window'
export type { WindowProps, BarProps, TextProps, PromptProps } from './window'

// Motion: behaviors that have to hold up frame by frame (motion/, one file per part)
export { Connector, Count, Roll, Swap, Collapse, Fly, Follow, cellOf, States, Stream, Popover, Camera, Scroll, Expand } from './motion'
export type { ConnectorProps, CountProps, FollowProps, FlyProps, PopoverOrigin } from './motion'
export { EASE, bezier, css as easeCss } from './ease'
export type { EaseName, Bezier } from './ease'

// Small parts: starting points with quiet defaults; restyle them as the product does (parts/, controls/)
export { Avatar, Check, Spinner, Toast, Tooltip, Meter, Menu, Facepile, Pin, Row, Card, Kbd, Divider } from './parts'
export type { AvatarProps, PinProps, RowProps } from './parts'
export { Segmented, Toggle, Tabs, Badge, Button, Draft, Slot } from './controls'
export type { ButtonProps, DraftProps } from './controls'
