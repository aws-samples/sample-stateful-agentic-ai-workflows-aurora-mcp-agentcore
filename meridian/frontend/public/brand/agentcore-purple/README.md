# AgentCore purple service icons

Original, unmodified SVGs from `Agentcore-Bedrock-Icons.pptx`, the purple
`#7B27FF` set:

- Un-suffixed files come from slide 3: black strokes for light backgrounds.
- `-dark` files come from slide 4: white strokes for dark backgrounds.

| File | Service | Slide 3 entry | Slide 4 entry |
| --- | --- | --- | --- |
| `agentcore.svg` | Amazon Bedrock AgentCore | `ppt/media/image27.svg` | `ppt/media/image47.svg` |
| `runtime.svg` | AgentCore Runtime | `ppt/media/image37.svg` | `ppt/media/image63.svg` |
| `gateway.svg` | AgentCore Gateway | `ppt/media/image43.svg` | `ppt/media/image53.svg` |
| `memory.svg` | AgentCore Memory | `ppt/media/image29.svg` | `ppt/media/image51.svg` |
| `policy.svg` | AgentCore Policy | `ppt/media/image41.svg` | `ppt/media/image57.svg` |

`ServiceMark` loads the light file; in the dark theme `meridianShowcase.css`
swaps in the `-dark` file with `content: url()`. The briefing architecture
diagram renders both and shows one per theme.
