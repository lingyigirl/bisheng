// @ts-strict-ignore
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import type { KnowledgeGraphData } from "@/controllers/API/knowledgeGraph"

declare const __APP_ENV__: any;

const PALETTE = [
    "#4D9BF0", "#00B42A", "#FF7D00", "#F53F3F", "#722ED1",
    "#14C9C9", "#F7BA1E", "#86909C", "#D91AD9", "#2B5AED",
]

// ECharts is loaded as a global script by the platform (see Dashboard/BaseChart).
// Re-implement the small loader here so this feature does not add an npm dep.
const loadECharts = async () => {
    if ((window as any).echarts) return (window as any).echarts
    const script = document.createElement("script")
    script.src = `${__APP_ENV__?.BASE_URL || ""}/echarts.min.js`
    script.type = "module"
    return new Promise((resolve, reject) => {
        script.onload = () => {
            const timer = setInterval(() => {
                if ((window as any).echarts) {
                    clearInterval(timer)
                    resolve((window as any).echarts)
                }
            }, 100)
        }
        script.onerror = reject
        document.head.appendChild(script)
    })
}

interface GraphCanvasProps {
    graph: KnowledgeGraphData | null
    onNodeClick?: (entityId: number) => void
}

function buildOption(graph: KnowledgeGraphData, typeLabel: string) {
    const nodes = graph.nodes || []
    const edges = graph.edges || []
    const categories = Array.from(new Set(nodes.map((node) => node.type)))
    const categoryIndex: Record<string, number> = {}
    categories.forEach((name, index) => { categoryIndex[name] = index })
    const maxMention = Math.max(1, ...nodes.map((node) => node.mention_count || 0))

    return {
        color: PALETTE,
        tooltip: {
            trigger: "item",
            confine: true,
            formatter: (params: any) => {
                if (params.dataType === "edge") {
                    const data = params.data || {}
                    return `<div style="max-width:260px;white-space:normal"><b>${data.label || ""}</b>${
                        data.description ? `<br/>${data.description}` : ""
                    }</div>`
                }
                const data = params.data || {}
                return `<div style="max-width:260px;white-space:normal"><b>${data.name || ""}</b>` +
                    `<br/>${typeLabel}${data.type || ""}` +
                    (data.description ? `<br/>${data.description}` : "") +
                    `</div>`
            },
        },
        legend: [{
            type: "scroll",
            bottom: 0,
            data: categories,
            textStyle: { fontSize: 11, color: "#4E5969" },
        }],
        series: [{
            type: "graph",
            layout: "force",
            roam: true,
            draggable: true,
            force: {
                repulsion: 240,
                edgeLength: [90, 170],
                gravity: 0.08,
                friction: 0.2,
            },
            categories: categories.map((name) => ({ name })),
            data: nodes.map((node) => ({
                id: String(node.id),
                name: node.name,
                type: node.type,
                description: node.description,
                category: categoryIndex[node.type] ?? 0,
                value: node.mention_count,
                symbolSize: Math.min(18 + ((node.mention_count || 0) / maxMention) * 30, 56),
            })),
            edges: edges.map((edge) => ({
                source: String(edge.source),
                target: String(edge.target),
                label: edge.label,
                description: edge.description,
                value: edge.weight,
                lineStyle: { width: 1 + Math.min(edge.weight || 0, 1) * 3 },
            })),
            label: { show: true, position: "right", fontSize: 11, formatter: "{b}" },
            edgeLabel: { show: false },
            emphasis: {
                focus: "adjacency",
                edgeLabel: {
                    show: true,
                    formatter: (params: any) => params.data?.label || "",
                },
            },
            lineStyle: { color: "source", curveness: 0.08, opacity: 0.65 },
            itemStyle: { opacity: 0.95 },
        }],
    }
}

export function GraphCanvas({ graph, onNodeClick }: GraphCanvasProps) {
    const { t } = useTranslation("knowledge")
    const domRef = useRef<HTMLDivElement>(null)
    const chartRef = useRef<any>(null)
    const [ready, setReady] = useState(false)

    useEffect(() => {
        let disposed = false
        loadECharts()
            .then((echarts) => {
                if (disposed || !domRef.current) return
                chartRef.current = echarts.init(domRef.current)
                const observer = new ResizeObserver(() => chartRef.current?.resize())
                observer.observe(domRef.current)
                ;(chartRef.current as any).__observer = observer
                setReady(true)
            })
            .catch((error) => console.error("Failed to load ECharts:", error))

        return () => {
            disposed = true
            const observer = (chartRef.current as any)?.__observer
            observer?.disconnect?.()
            chartRef.current?.dispose?.()
            chartRef.current = null
        }
    }, [])

    useEffect(() => {
        const chart = chartRef.current
        if (!chart || !graph || !ready) return
        chart.setOption(buildOption(graph, t("knowledgeGraph.nodeType")), true)
        chart.off("click")
        chart.on("click", (params: any) => {
            if (params.dataType === "node") {
                onNodeClick?.(Number(params.data.id))
            }
        })
    }, [graph, onNodeClick, t, ready])

    if (!graph || graph.nodes.length === 0) {
        return null
    }

    return <div ref={domRef} className="size-full min-h-[360px]" />
}
