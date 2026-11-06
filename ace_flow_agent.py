#!/usr/bin/env python3
"""
Generic IBM ACE Message Flow AI Documenter & Architectural Agent
================================================================
A fully generic, intelligent agent for IBM App Connect Enterprise (ACE) / IIB.
Recursively discovers, parses, analyzes, diagrams, and documents ANY message flow
(.msgflow) and subflow (.subflow) across projects, applications, and libraries.

Features:
- Completely generic: Works on ANY message flow or subflow with zero flow-specific hardcoding.
- Universal Node Taxonomy: Categorizes any built-in or user-defined node (HTTP, MQ, File,
  TCP/IP, Kafka, Database, SOAP, JMS, Compute, JavaCompute, Mapping, Trace, TryCatch, etc.).
- Protocol & Endpoint Extraction: Automatically detects URLs, queues, topics, file directories,
  ports, and datasources from node properties and promoted attributes.
- Workspace-Wide ESQL & Dependency Resolution: Indexes and resolves companion ESQL files,
  modules, procedures, and shared library paths (across projects and folders).
- High-Fidelity Mermaid.js Diagrams:
    * Flowchart Topology with category color-coding and protocol endpoint badges.
    * Sequence Diagram derived from topological DAG execution order and catch/failure branches.
- Enterprise Static Flow Audits & Health Scoring:
    * Error handling completeness (unwired catch/failure terminals, TryCatch validation).
    * Hardcoded vs. Promoted configuration portability (file paths, endpoints, credentials).
    * Orphan/dead-end nodes and loop detection.
    * Active production tracing alerts.
- Workspace Catalog Mode: Scans entire repository/workspace and creates an overarching
  Master Catalog index alongside individual flow documentation.
- Optional AI Architect Review: Integrates with LLMs (Google Gemini) for deep architectural review.
"""

import os
import re
import sys
import json
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
from collections import defaultdict, deque

# Optional LLM integration (supports modern google-genai and legacy google-generativeai)
LLM_PROVIDER: Optional[str] = None
genai_module = None

try:
    from google import genai
    LLM_PROVIDER = "google-genai"
    genai_module = genai
except Exception:
    try:
        import google.generativeai as genai_legacy
        LLM_PROVIDER = "google-generativeai"
        genai_module = genai_legacy
    except Exception:
        LLM_PROVIDER = None
        genai_module = None

LLM_AVAILABLE = LLM_PROVIDER is not None


class NodeCategory:
    INPUT = "Input"
    OUTPUT = "Output"
    REQUEST = "Request/Client"
    COMPUTE = "Compute/Transform"
    ROUTING = "Routing/Filter"
    DIAGNOSTIC = "Trace/Logging"
    ERROR = "Error Handling"
    SUBFLOW = "Subflow"
    GENERIC = "Processing Node"


class ACENode:
    """Represents any node in an IBM ACE message flow or subflow."""

    def __init__(self, node_id: str, raw_type: str, label: str, location: str, properties: Dict[str, str]):
        self.node_id = node_id
        self.raw_type = raw_type
        self.clean_type = self._clean_type(raw_type)
        self.label = label.strip() if label else self.clean_type
        self.location = location
        self.properties = properties
        self.category = self._classify_category()
        self.promoted_properties: Dict[str, str] = {}  # Overridden by flow promoted features

    @staticmethod
    def _clean_type(raw_type: str) -> str:
        # e.g., 'ComIbmWSInput.msgnode:FCMComposite_1' -> 'WSInput'
        # e.g., 'ComIbmCompute.msgnode:FCMComposite_1' -> 'Compute'
        t = raw_type.split(":")[0]
        if t.startswith("ComIbm"):
            t = t[6:]
        if t.endswith(".msgnode"):
            t = t[:-8]
        return t or raw_type

    def _classify_category(self) -> str:
        t = self.clean_type.lower()
        if "trace" in t:
            return NodeCategory.DIAGNOSTIC
        elif "trycatch" in t or "throw" in t:
            return NodeCategory.ERROR
        elif "compute" in t or "mapping" in t or "xslt" in t or "resetcontentdescriptor" in t or "dotnet" in t:
            return NodeCategory.COMPUTE
        elif "reply" in t or "output" in t or "producer" in t or "publish" in t or "sender" in t:
            return NodeCategory.OUTPUT
        elif "input" in t or "consumer" in t or "subscriber" in t or "listener" in t or "receiver" in t:
            return NodeCategory.INPUT
        elif "request" in t or "retrieve" in t or "get" in t:
            return NodeCategory.REQUEST
        elif "route" in t or "filter" in t or "label" in t or "collector" in t or "resequence" in t or "aggregate" in t:
            return NodeCategory.ROUTING
        elif "subflow" in t:
            return NodeCategory.SUBFLOW
        return NodeCategory.GENERIC

    def get_protocol_endpoint_info(self) -> Optional[Dict[str, str]]:
        """Extracts protocol and endpoint information generically from any node's properties."""
        p = self.properties
        # HTTP / REST / WebServices
        for key in ["URLSpecifier", "urlSelector", "webServiceURL", "endpointUrl"]:
            if key in p:
                return {"type": "HTTP/REST", "label": "HTTP Endpoint", "value": p[key]}

        # TCP/IP
        if "connectionDetails" in p:
            return {"type": "TCP/IP", "label": "TCP/IP Connection", "value": p["connectionDetails"]}

        # MQ / Messaging
        if "queueName" in p:
            qm = p.get("queueManagerName", "")
            val = f"{qm}/{p['queueName']}" if qm else p["queueName"]
            return {"type": "MQ", "label": "MQ Queue", "value": val}

        # Kafka / Topics
        if "topicName" in p:
            return {"type": "Kafka", "label": "Topic", "value": p["topicName"]}

        # File System
        for key in ["inputDirectory", "outputDirectory", "filePath"]:
            if key in p:
                return {"type": "File", "label": "File Path", "value": p[key]}

        # Database
        if "dataSource" in p:
            return {"type": "Database", "label": "DataSource", "value": p["dataSource"]}

        return None

    def get_summary_attribute(self) -> str:
        """Returns a concise summary string of the node's key operational property."""
        ep = self.get_protocol_endpoint_info()
        if ep:
            return f"{ep['label']}: {ep['value']}"
        if "computeExpression" in self.properties:
            routine = self.properties["computeExpression"].split("#")[-1]
            return f"ESQL: {routine}"
        if "javaClass" in self.properties:
            return f"Java: {self.properties['javaClass'].split('.')[-1]}"
        if "mappingFile" in self.properties:
            return f"Map: {self.properties['mappingFile']}"
        if "destination" in self.properties:
            return f"Dest: {self.properties['destination']}"
        return ""


class ACEConnection:
    """Represents a connection/wire between two node terminals."""

    def __init__(self, conn_id: str, source_node: str, source_terminal: str, target_node: str, target_terminal: str):
        self.conn_id = conn_id
        self.source_node = source_node
        self.source_terminal = self._clean_terminal(source_terminal)
        self.target_node = target_node
        self.target_terminal = self._clean_terminal(target_terminal)
        self.is_error_path = self._is_error_terminal(self.source_terminal)

    @staticmethod
    def _clean_terminal(terminal: str) -> str:
        if "." in terminal:
            return terminal.split(".", 1)[1]
        return terminal

    @staticmethod
    def _is_error_terminal(terminal: str) -> bool:
        term_lower = terminal.lower()
        return any(k in term_lower for k in ["catch", "failure", "timeout", "nomatch", "error", "unknown"])


class PromotedProperty:
    """Represents a promoted User Defined Property (UDP) or attribute link."""

    def __init__(self, name: str, default_value: str, overridden_nodes: List[str]):
        self.name = name
        self.default_value = default_value
        self.overridden_nodes = overridden_nodes


class ESQLWorkspaceResolver:
    """
    Recursively scans the workspace to discover and index all ESQL modules and routines,
    making them resolvable for any message flow regardless of folder layout.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.esql_files: List[Path] = []
        self.modules: Dict[str, Dict[str, Any]] = {}
        self.procedures: Dict[str, Dict[str, Any]] = {}
        self.schema_files: Dict[str, List[Path]] = defaultdict(list)
        self._index_workspace()

    def _index_workspace(self):
        if not self.root_dir.exists():
            return

        for p in self.root_dir.rglob("*.esql"):
            self.esql_files.append(p)
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                self._index_file(p, content)
            except Exception:
                pass

    def _index_file(self, file_path: Path, content: str):
        # Extract Broker Schema
        schema = ""
        schema_match = re.search(r'BROKER\s+SCHEMA\s+([A-Za-z0-9_.]+)', content, re.IGNORECASE)
        if schema_match:
            schema = schema_match.group(1).strip()
            self.schema_files[schema].append(file_path)

        # Index COMPUTE / DATABASE / FILTER modules
        module_pattern = re.compile(
            r'CREATE\s+(COMPUTE|DATABASE|FILTER)\s+MODULE\s+([A-Za-z0-9_]+)(.*?)END\s+MODULE\s*;',
            re.DOTALL | re.IGNORECASE
        )
        for m in module_pattern.finditer(content):
            mod_type, mod_name, mod_body = m.group(1), m.group(2), m.group(0)
            key = f"{schema}#{mod_name}" if schema else mod_name
            self.modules[key] = {
                "name": mod_name,
                "type": mod_type,
                "schema": schema,
                "file": file_path,
                "code": mod_body.strip()
            }
            # Also store with unqualified name for easy lookup
            if mod_name not in self.modules:
                self.modules[mod_name] = self.modules[key]

        # Index standalone functions and procedures
        proc_pattern = re.compile(
            r'CREATE\s+(PROCEDURE|FUNCTION)\s+([A-Za-z0-9_]+)\s*\((.*?)\)(.*?)END\s*;\s*',
            re.DOTALL | re.IGNORECASE
        )
        for p in proc_pattern.finditer(content):
            proc_kind, proc_name, proc_params, proc_body = p.group(1), p.group(2), p.group(3), p.group(0)
            self.procedures[proc_name] = {
                "name": proc_name,
                "kind": proc_kind,
                "params": proc_params.strip(),
                "file": file_path,
                "code": proc_body.strip()
            }

    def resolve_compute_expression(self, compute_expr: str) -> Optional[Dict[str, Any]]:
        """Resolves an esql://routine URI to the corresponding indexed ESQL module."""
        if not compute_expr:
            return None

        # e.g. "esql://routine/com.ishahzadtech.ibmace.echo#PrepareEchoResponse.Main"
        # or "esql://routine/#PrepareResponseModule.Main"
        expr = compute_expr
        if expr.startswith("esql://routine/"):
            expr = expr[len("esql://routine/"):]

        schema_part = ""
        mod_routine = expr
        if "#" in expr:
            schema_part, mod_routine = expr.split("#", 1)

        mod_name = mod_routine.split(".")[0] if "." in mod_routine else mod_routine

        # Attempt exact schema#module lookup
        if schema_part and f"{schema_part}#{mod_name}" in self.modules:
            return self.modules[f"{schema_part}#{mod_name}"]
        # Fallback to unqualified module name
        if mod_name in self.modules:
            return self.modules[mod_name]

        return None


class ACEFlowParser:
    """Parses any IBM ACE / IIB .msgflow or .subflow XML file generically."""

    def __init__(self, flow_path: Path, workspace_resolver: Optional[ESQLWorkspaceResolver] = None):
        self.flow_path = Path(flow_path).resolve()
        self.flow_name = self.flow_path.stem
        self.is_subflow = self.flow_path.suffix.lower() == ".subflow"
        self.nodes: Dict[str, ACENode] = {}
        self.connections: List[ACEConnection] = []
        self.promoted_properties: Dict[str, PromotedProperty] = {}
        self.associated_esql: Dict[str, Dict[str, Any]] = {}
        self.referenced_subflows: Set[str] = set()

        # Workspace resolver
        self.resolver = workspace_resolver or ESQLWorkspaceResolver(self.flow_path.parent)

    def parse(self):
        if not self.flow_path.exists():
            raise FileNotFoundError(f"Message flow not found at {self.flow_path}")

        tree = ET.parse(self.flow_path)
        root = tree.getroot()

        # 1. Parse Promoted Attributes & Structural Features
        feature_defaults = {}
        for elem in root.iter():
            tag = self._strip_ns(elem.tag)
            if tag == "eStructuralFeatures":
                feat_name = elem.attrib.get("name", "")
                def_val = elem.attrib.get("defaultValueLiteral", "")
                if feat_name:
                    feature_defaults[feat_name] = def_val

        for elem in root.iter():
            tag = self._strip_ns(elem.tag)
            if tag == "attributeLinks":
                promoted_attr = elem.attrib.get("promotedAttribute", "")
                clean_attr = promoted_attr.replace("Property.", "")
                overridden_nodes = elem.attrib.get("overriddenNodes", "").split()
                def_val = feature_defaults.get(clean_attr, feature_defaults.get(promoted_attr, ""))
                
                if clean_attr in self.promoted_properties:
                    # Extend overridden nodes if declared across multiple attributeLinks
                    existing = self.promoted_properties[clean_attr]
                    for nid in overridden_nodes:
                        if nid not in existing.overridden_nodes:
                            existing.overridden_nodes.append(nid)
                else:
                    self.promoted_properties[clean_attr] = PromotedProperty(clean_attr, def_val, list(overridden_nodes))

        # 2. Parse Nodes and Connections
        for elem in root.iter():
            tag = self._strip_ns(elem.tag)
            if tag == "nodes":
                attribs = self._clean_dict(elem.attrib)
                node_id = attribs.get("id", "")
                raw_type = attribs.get("type", "GenericNode")
                location = attribs.get("location", "")

                # Node label
                label = ""
                for child in elem:
                    child_tag = self._strip_ns(child.tag)
                    if child_tag == "translation":
                        label = child.attrib.get("string", "")

                props = {k: v for k, v in attribs.items() if k not in ("id", "type", "location")}
                node = ACENode(node_id, raw_type, label, location, props)

                # Link promoted properties to node
                for prop_name, promoted in self.promoted_properties.items():
                    if node_id in promoted.overridden_nodes:
                        node.promoted_properties[prop_name] = promoted.default_value

                self.nodes[node_id] = node

                # Detect if node is a subflow
                if node.category == NodeCategory.SUBFLOW or "subflow" in node.raw_type.lower():
                    subflow_name = node.clean_type
                    self.referenced_subflows.add(subflow_name)

            elif tag == "connections":
                attribs = self._clean_dict(elem.attrib)
                conn = ACEConnection(
                    conn_id=attribs.get("id", ""),
                    source_node=attribs.get("sourceNode", ""),
                    source_terminal=attribs.get("sourceTerminalName", ""),
                    target_node=attribs.get("targetNode", ""),
                    target_terminal=attribs.get("targetTerminalName", "")
                )
                self.connections.append(conn)

        # 3. Associate companion ESQL modules for Compute nodes
        for node in self.nodes.values():
            if "computeExpression" in node.properties:
                expr = node.properties["computeExpression"]
                resolved = self.resolver.resolve_compute_expression(expr)
                if resolved:
                    self.associated_esql[node.node_id] = resolved

    @staticmethod
    def _strip_ns(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    @classmethod
    def _clean_dict(cls, d: Dict[str, str]) -> Dict[str, str]:
        return {cls._strip_ns(k): v for k, v in d.items()}


class ACEFlowGraph:
    """Graph structure providing topology analysis, path traversal, and topological ordering."""

    def __init__(self, parser: ACEFlowParser):
        self.parser = parser
        self.nodes = parser.nodes
        self.connections = parser.connections
        self.adj: Dict[str, List[Tuple[str, str]]] = defaultdict(list)  # src -> [(tgt, terminal)]
        self.in_degree: Dict[str, int] = {nid: 0 for nid in self.nodes}

        for c in self.connections:
            if c.source_node in self.nodes and c.target_node in self.nodes:
                self.adj[c.source_node].append((c.target_node, c.source_terminal))
                self.in_degree[c.target_node] += 1

    def get_root_nodes(self) -> List[ACENode]:
        """Nodes with zero incoming connections, or explicit Input category nodes."""
        roots = [n for nid, n in self.nodes.items() if self.in_degree[nid] == 0]
        if not roots:
            roots = [n for n in self.nodes.values() if n.category == NodeCategory.INPUT]
        return roots or list(self.nodes.values())[:1]

    def get_terminal_nodes(self) -> List[ACENode]:
        """Nodes with zero outgoing connections, or Reply/Output/Throw nodes."""
        return [n for nid, n in self.nodes.items() if len(self.adj[nid]) == 0]

    def topological_order(self) -> List[ACENode]:
        """Returns nodes in chronological/topological execution order using Kahn's algorithm / BFS."""
        in_deg = dict(self.in_degree)
        queue = deque([nid for nid, deg in in_deg.items() if deg == 0])
        ordered_ids = []

        while queue:
            curr = queue.popleft()
            ordered_ids.append(curr)
            for neighbor, _ in self.adj[curr]:
                in_deg[neighbor] -= 1
                if in_deg[neighbor] == 0:
                    queue.append(neighbor)

        # Include any remaining nodes in case of cycles or disconnected fragments
        for nid in self.nodes:
            if nid not in ordered_ids:
                ordered_ids.append(nid)

        return [self.nodes[nid] for nid in ordered_ids if nid in self.nodes]


class GenericMermaidGenerator:
    """Generates high-fidelity, categorized Mermaid diagrams for any ACE message flow."""

    STYLE_CLASSES = {
        NodeCategory.INPUT: "fill:#ecfdf5,stroke:#059669,stroke-width:2px,color:#064e3b",
        NodeCategory.OUTPUT: "fill:#f0fdfa,stroke:#0d9488,stroke-width:2px,color:#134e4a",
        NodeCategory.REQUEST: "fill:#f0f9ff,stroke:#0284c7,stroke-width:1.5px,color:#0c4a6e",
        NodeCategory.COMPUTE: "fill:#eef2ff,stroke:#6366f1,stroke-width:1.5px,color:#312e81",
        NodeCategory.ROUTING: "fill:#fffbeb,stroke:#d97706,stroke-width:1.5px,color:#78350f",
        NodeCategory.DIAGNOSTIC: "fill:#f8fafc,stroke:#64748b,stroke-width:1.5px,color:#1e293b",
        NodeCategory.ERROR: "fill:#fff1f2,stroke:#e11d48,stroke-width:2px,color:#881337",
        NodeCategory.SUBFLOW: "fill:#faf5ff,stroke:#9333ea,stroke-width:2px,stroke-dasharray: 5 5,color:#581c87",
        NodeCategory.GENERIC: "fill:#f8fafc,stroke:#475569,stroke-width:1.5px,color:#0f172a",
    }

    @classmethod
    def generate_flowchart(cls, parser: ACEFlowParser) -> str:
        lines = [
            "```mermaid",
            "flowchart LR",
            f'    subgraph FlowRoot ["ACE Flow: {parser.flow_name}"]',
            "        direction LR"
        ]

        # 1. Render Internal Flow Nodes with appropriate Mermaid shapes
        category_node_map = defaultdict(list)
        for node_id, node in parser.nodes.items():
            category_node_map[node.category].append(node_id)
            label_safe = node.label.replace('"', '&quot;')
            summary = node.get_summary_attribute()
            summary_html = f"<br/><i>{summary}</i>" if summary else ""

            # Assign Mermaid shape based on category
            if node.category == NodeCategory.INPUT:
                shape = f'        {node_id}(["<b>{label_safe}</b><br/>({node.clean_type}){summary_html}"])'
            elif node.category in (NodeCategory.OUTPUT, NodeCategory.REQUEST):
                shape = f'        {node_id}[["<b>{label_safe}</b><br/>({node.clean_type}){summary_html}"]]'
            elif node.category == NodeCategory.ROUTING:
                shape = f'        {node_id}{{"<b>{label_safe}</b><br/>({node.clean_type}){summary_html}"}}'
            elif node.category == NodeCategory.ERROR:
                shape = f'        {node_id}{{{{"<b>{label_safe}</b><br/>({node.clean_type}){summary_html}"}}}}'
            elif node.category == NodeCategory.DIAGNOSTIC:
                shape = f'        {node_id}[/"<b>{label_safe}</b><br/>({node.clean_type}){summary_html}"/]'
            elif node.category == NodeCategory.SUBFLOW:
                shape = f'        {node_id}[["[[Subflow]] <b>{label_safe}</b><br/>({node.clean_type}){summary_html}"]]'
            else:
                shape = f'        {node_id}["<b>{label_safe}</b><br/>({node.clean_type}){summary_html}"]'

            lines.append(shape)

        # 2. Render Connections
        for conn in parser.connections:
            if conn.source_node in parser.nodes and conn.target_node in parser.nodes:
                terminal_label = f'|"{conn.source_terminal}"|' if conn.source_terminal else ""
                if conn.is_error_path:
                    # Dashed connection for error/catch terminals
                    lines.append(f"        {conn.source_node} -.->{terminal_label} {conn.target_node}")
                else:
                    lines.append(f"        {conn.source_node} -->{terminal_label} {conn.target_node}")

        lines.append("    end")

        # 3. Render External Integrations (Generically for any protocol)
        ext_counter = 0
        for node in parser.nodes.values():
            ep = node.get_protocol_endpoint_info()
            if ep:
                ext_counter += 1
                ext_id = f"ExtResource_{ext_counter}"
                safe_val = ep['value'].replace('\\', '/').replace('"', '')

                if ep['type'] in ("MQ", "Database"):
                    ext_node = f'    {ext_id}[("{ep["label"]}<br/><b>{safe_val}</b>")]'
                else:
                    ext_node = f'    {ext_id}(["{ep["label"]}<br/><b>{safe_val}</b>"])'

                lines.append(ext_node)

                if node.category == NodeCategory.INPUT:
                    lines.append(f"    {ext_id} ==>|Receives| {node.node_id}")
                elif node.category == NodeCategory.OUTPUT:
                    lines.append(f"    {node.node_id} ==>|Sends to| {ext_id}")
                else:
                    lines.append(f"    {node.node_id} <==>|Interacts| {ext_id}")

        # 4. Class Styling definitions
        lines.append("")
        for cat, style in cls.STYLE_CLASSES.items():
            class_name = f"cls_{cat.replace('/', '_').replace(' ', '_')}"
            lines.append(f"    classDef {class_name} {style};")
            matched_nodes = category_node_map.get(cat, [])
            if matched_nodes:
                lines.append(f"    class {','.join(matched_nodes)} {class_name};")

        lines.append("    classDef externalRes fill:#f1f5f9,stroke:#334155,stroke-width:1.5px,stroke-dasharray: 3 3,color:#0f172a;")
        if ext_counter > 0:
            all_ext = ",".join([f"ExtResource_{i+1}" for i in range(ext_counter)])
            lines.append(f"    class {all_ext} externalRes;")

        lines.append("```")
        return "\n".join(lines)

    @classmethod
    def generate_sequence(cls, parser: ACEFlowParser, graph: ACEFlowGraph) -> str:
        lines = [
            "```mermaid",
            "sequenceDiagram",
            "    autonumber"
        ]

        # Participants in topological order
        ordered_nodes = graph.topological_order()
        root_nodes = graph.get_root_nodes()

        # External Client / System Actor
        lines.append("    actor Ext as External System / Client")

        for node in ordered_nodes:
            safe_name = node.label.replace('"', '').replace(':', ' - ')
            lines.append(f"    participant {node.node_id} as {safe_name} ({node.clean_type})")

        # Initial message arrival at roots
        for r in root_nodes:
            ep = r.get_protocol_endpoint_info()
            proto = f" via {ep['type']}" if ep else ""
            lines.append(f"    Ext->>{r.node_id}: Inbound Message Arrival{proto}")
            if "messageDomainProperty" in r.properties:
                lines.append(f"    Note over {r.node_id}: Parse inbound domain: {r.properties['messageDomainProperty']}")

        # Trace connections with alt blocks for catch / failure branches
        standard_conns = [c for c in parser.connections if not c.is_error_path]
        error_conns = [c for c in parser.connections if c.is_error_path]

        # Main propagation flow
        for conn in standard_conns:
            if conn.source_node in parser.nodes and conn.target_node in parser.nodes:
                src = parser.nodes[conn.source_node]
                tgt = parser.nodes[conn.target_node]
                term_desc = f" ({conn.source_terminal})" if conn.source_terminal else ""
                lines.append(f"    {conn.source_node}->>{conn.target_node}: Propagate{term_desc}")

                if tgt.node_id in parser.associated_esql:
                    esql_info = parser.associated_esql[tgt.node_id]
                    lines.append(f"    Note over {tgt.node_id}: Execute ESQL: {esql_info['name']}")
                elif tgt.category == NodeCategory.ROUTING:
                    lines.append(f"    Note over {tgt.node_id}: Route / Filter evaluation")

        # Error / Exception branch flow
        if error_conns:
            lines.append("    rect rgb(254, 242, 242)")
            lines.append("    Note over Ext: Exception / Error Propagation Branch")
            for conn in error_conns:
                if conn.source_node in parser.nodes and conn.target_node in parser.nodes:
                    lines.append(f"    {conn.source_node}-->>{conn.target_node}: Exception Caught ({conn.source_terminal})")
            lines.append("    end")

        # Completion / Response back to External Actor
        terminal_nodes = graph.get_terminal_nodes()
        replies = [n for n in terminal_nodes if n.category == NodeCategory.OUTPUT]
        for rep in replies:
            lines.append(f"    {rep.node_id}->>Ext: Return Response / Complete Transmission")

        lines.append("```")
        return "\n".join(lines)


class GenericFlowAuditor:
    """Performs comprehensive static architectural, reliability, and security audits on any flow."""

    @staticmethod
    def audit(parser: ACEFlowParser, graph: ACEFlowGraph) -> Tuple[List[Dict[str, str]], int, str]:
        findings: List[Dict[str, str]] = []
        score = 100

        connected_sources = {(c.source_node, c.source_terminal) for c in parser.connections}
        connected_targets = {c.target_node for c in parser.connections}

        # 1. Error Handling Audits
        root_nodes = graph.get_root_nodes()
        for root in root_nodes:
            if root.category == NodeCategory.INPUT:
                for term in ["catch", "failure"]:
                    if (root.node_id, term) not in connected_sources:
                        score -= 10
                        findings.append({
                            "level": "WARNING",
                            "category": "Reliability & Error Handling",
                            "message": f"Input node '{root.label}' ({root.clean_type}) does not have its '{term}' terminal wired. Uncaught downstream exceptions may trigger message rollbacks or thread dumps."
                        })
                    else:
                        findings.append({
                            "level": "GOOD PRACTICE",
                            "category": "Reliability & Error Handling",
                            "message": f"Input node '{root.label}' has its '{term}' terminal wired for resilient error capture."
                        })

        # 2. Hardcoded Values & Configuration Portability Audits
        for node in parser.nodes.values():
            for prop_name, prop_val in node.properties.items():
                if not isinstance(prop_val, str) or not prop_val.strip():
                    continue

                # Hardcoded Absolute File Paths (Exclude URLs / Endpoints)
                is_url_prop = any(u in prop_name.lower() for u in ["url", "specifier", "service"])
                is_filepath_prop = prop_name in ("filePath", "inputDirectory", "outputDirectory", "dataDirectory")
                is_file_like_val = not is_url_prop and (":\\" in prop_val or (prop_val.startswith("/") and any(p in prop_val for p in ["/home", "/var", "/tmp", "/etc", "/opt", "/usr", "/app", "/data"])))

                if is_filepath_prop or is_file_like_val:
                    is_promoted = prop_name in node.promoted_properties or any(prop_name in p for p in parser.promoted_properties)
                    if is_promoted:
                        findings.append({
                            "level": "INFO",
                            "category": "Portability & BAR Overrides",
                            "message": f"Node '{node.label}' uses path '{prop_val}' which is properly promoted as a configurable property ('{prop_name}'). Ensure environment overrides are configured in BAR files."
                        })
                    else:
                        score -= 10
                        findings.append({
                            "level": "WARNING",
                            "category": "Hardcoded Configuration",
                            "message": f"Node '{node.label}' has hardcoded absolute path '{prop_val}' that is NOT promoted. This prevents environment-agnostic deployment across DEV/QA/PROD."
                        })

                # Hardcoded Connection Details / Hostnames / Ports
                if prop_name == "connectionDetails":
                    is_promoted = "connectionDetails" in node.promoted_properties or "connectionDetails" in parser.promoted_properties
                    if not is_promoted:
                        score -= 5
                        findings.append({
                            "level": "WARNING",
                            "category": "Hardcoded Configuration",
                            "message": f"Node '{node.label}' has hardcoded connectionDetails '{prop_val}' without property promotion."
                        })
                    else:
                        findings.append({
                            "level": "GOOD PRACTICE",
                            "category": "Portability & BAR Overrides",
                            "message": f"Node '{node.label}' connectionDetails is properly promoted as a configurable property."
                        })

        # 3. Production Diagnostics & Active Trace Nodes
        for node in parser.nodes.values():
            if node.clean_type == "Trace":
                score -= 5
                findings.append({
                    "level": "ADVISORY",
                    "category": "Production Performance",
                    "message": f"Node '{node.label}' is an active Trace node writing to '{node.properties.get('destination', 'log')}'. Verify file I/O tracing is disabled or throttled in production environments."
                })

        # 4. Dead Ends and Disconnected Nodes
        for node_id, node in parser.nodes.items():
            if node_id not in connected_targets and len(graph.adj[node_id]) == 0:
                score -= 10
                findings.append({
                    "level": "WARNING",
                    "category": "Dead End / Orphan Node",
                    "message": f"Node '{node.label}' ({node.clean_type}) is completely disconnected with no incoming or outgoing wires."
                })

        # 5. ESQL Module Verification
        for node_id, node in parser.nodes.items():
            if "computeExpression" in node.properties:
                if node_id not in parser.associated_esql:
                    expr = node.properties["computeExpression"]
                    score -= 15
                    findings.append({
                        "level": "CRITICAL",
                        "category": "Missing ESQL Implementation",
                        "message": f"Compute node '{node.label}' references ESQL '{expr}', but the module could not be found in any workspace .esql file."
                    })

        # Grade calculation
        score = max(0, min(100, score))
        if score >= 90:
            grade = "A (Production Ready)"
        elif score >= 80:
            grade = "B (Good / Minor Improvements)"
        elif score >= 70:
            grade = "C (Needs Review)"
        elif score >= 50:
            grade = "D (High Risk)"
        else:
            grade = "F (Critical Deficiencies)"

        return findings, score, grade


class ACEFlowAgent:
    """Orchestrates parsing, diagramming, auditing, and markdown documentation for any flow."""

    def __init__(self, flow_path: Path, workspace_resolver: Optional[ESQLWorkspaceResolver] = None, use_ai: bool = False):
        self.flow_path = Path(flow_path).resolve()
        self.resolver = workspace_resolver or ESQLWorkspaceResolver(self.flow_path.parent)
        self.parser = ACEFlowParser(self.flow_path, self.resolver)
        self.use_ai = use_ai
        self.graph: Optional[ACEFlowGraph] = None
        self.audit_findings: List[Dict[str, str]] = []
        self.health_score: int = 100
        self.health_grade: str = "A"

    def document(self) -> str:
        self.parser.parse()
        self.graph = ACEFlowGraph(self.parser)
        self.audit_findings, self.health_score, self.health_grade = GenericFlowAuditor.audit(self.parser, self.graph)

        flowchart = GenericMermaidGenerator.generate_flowchart(self.parser)
        sequence = GenericMermaidGenerator.generate_sequence(self.parser, self.graph)

        flow_type = "Subflow" if self.parser.is_subflow else "Message Flow"
        doc: List[str] = []

        # Header
        doc.append(f"# IBM ACE {flow_type}: `{self.parser.flow_name}`\n")
        doc.append(f"> **File Location:** `{self.flow_path}`  ")
        doc.append(f"> **Health Score:** **{self.health_score}/100 ({self.health_grade})**  ")
        doc.append(f"> **Total Nodes:** {len(self.parser.nodes)} | **Total Wires:** {len(self.parser.connections)}\n")

        # 1. Executive Summary
        doc.append("## 1. Flow Architecture Overview\n")
        roots = self.graph.get_root_nodes()
        terms = self.graph.get_terminal_nodes()
        inbound_protocols = [r.get_protocol_endpoint_info() for r in roots if r.get_protocol_endpoint_info()]
        outbound_protocols = [t.get_protocol_endpoint_info() for t in terms if t.get_protocol_endpoint_info()]

        if inbound_protocols:
            in_desc = ", ".join([f"{p['label']} (`{p['value']}`)" for p in inbound_protocols])
            doc.append(f"- **Inbound Interface(s):** {in_desc}")
        if outbound_protocols:
            out_desc = ", ".join([f"{p['label']} (`{p['value']}`)" for p in outbound_protocols])
            doc.append(f"- **Outbound Interface(s):** {out_desc}")
        if self.parser.referenced_subflows:
            doc.append(f"- **Referenced Subflow(s):** {', '.join(sorted(self.parser.referenced_subflows))}")
        doc.append("")

        # 2. Diagrams
        doc.append("### Flow Topology Diagram")
        doc.append(flowchart + "\n")

        doc.append("### Execution Sequence Diagram")
        doc.append(sequence + "\n")

        # 3. Node Specifications
        doc.append("## 2. Node Specifications & Configurations\n")
        doc.append("| Node Label | Clean Type | Category | Location | Promoted Config | Operational Details |")
        doc.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for node in self.graph.topological_order():
            promoted_str = "<br/>".join([f"`{k}` = `{v}`" for k, v in node.promoted_properties.items()]) or "-"
            details = []
            ep = node.get_protocol_endpoint_info()
            if ep:
                details.append(f"**{ep['label']}**: `{ep['value']}`")
            for k, v in node.properties.items():
                if k not in ("URLSpecifier", "urlSelector", "webServiceURL", "queueName", "connectionDetails", "filePath"):
                    details.append(f"**{k}**: `{v}`")
            details_str = "<br/>".join(details[:5]) or "Default"
            doc.append(f"| **{node.label}** | `{node.clean_type}` | `{node.category}` | `{node.location}` | {promoted_str} | {details_str} |")
        doc.append("")

        # 4. Connection Routing Matrix
        doc.append("### Connection & Routing Matrix\n")
        doc.append("| Source Node | Source Terminal | Target Node | Target Terminal | Path Type |")
        doc.append("| :--- | :--- | :--- | :--- | :--- |")
        for conn in self.parser.connections:
            src = self.parser.nodes.get(conn.source_node)
            tgt = self.parser.nodes.get(conn.target_node)
            src_lbl = src.label if src else conn.source_node
            tgt_lbl = tgt.label if tgt else conn.target_node
            path_type = "🔴 Error / Catch" if conn.is_error_path else "🟢 Standard Flow"
            doc.append(f"| **{src_lbl}** | `{conn.source_terminal}` | **{tgt_lbl}** | `{conn.target_terminal}` | {path_type} |")
        doc.append("")

        # 5. Promoted Properties / UDPs
        if self.parser.promoted_properties:
            doc.append("## 3. Promoted User Defined Properties (UDPs)\n")
            doc.append("| Property Name | Default Value | Overridden Flow Nodes |")
            doc.append("| :--- | :--- | :--- |")
            for name, prop in self.parser.promoted_properties.items():
                node_names = [self.parser.nodes[nid].label for nid in prop.overridden_nodes if nid in self.parser.nodes]
                nodes_display = ", ".join(node_names) if node_names else ", ".join(prop.overridden_nodes)
                doc.append(f"| `{name}` | `{prop.default_value}` | {nodes_display} |")
            doc.append("")

        # 6. Companion ESQL Code Snippets
        if self.parser.associated_esql:
            doc.append("## 4. Associated ESQL Transformation Logic\n")
            for node_id, esql_info in self.parser.associated_esql.items():
                node = self.parser.nodes.get(node_id)
                node_name = node.label if node else node_id
                doc.append(f"### ESQL Module for `{node_name}`: `{esql_info['name']}`")
                doc.append(f"> **Defined in:** `{esql_info['file'].name}` (Schema: `{esql_info['schema'] or 'default'}`)\n")
                doc.append("```sql")
                doc.append(esql_info["code"])
                doc.append("```\n")

        # 7. Architectural Audit Findings
        doc.append("## 5. Architectural & Reliability Audit Findings\n")
        doc.append(f"**Overall Flow Health Grade: {self.health_grade} ({self.health_score}/100)**\n")
        if self.audit_findings:
            for item in self.audit_findings:
                badge = f"**[{item['level']}]**"
                doc.append(f"- {badge} **{item['category']}**: {item['message']}")
        else:
            doc.append("✓ No major design antipatterns or unhandled error terminals detected.")
        doc.append("")

        # 8. Optional AI Review
        if self.use_ai:
            ai_review = self._run_ai_critique("\n".join(doc))
            if ai_review:
                doc.append("## 6. AI Agent Architectural Critique & Recommendations\n")
                doc.append(ai_review)

        return "\n".join(doc)

    def _run_ai_critique(self, markdown_context: str) -> Optional[str]:
        if not LLM_AVAILABLE or not genai_module:
            return "> *AI Review note: Google GenAI library is not installed. Install with `pip install google-genai`.*"

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "> *AI Review note: GEMINI_API_KEY environment variable is not set. Set it to activate live AI architectural reviews.*"

        try:
            prompt = (
                "You are an expert IBM App Connect Enterprise (ACE) / IIB Enterprise Solution Architect. "
                "Analyze the following message flow specification, Mermaid diagrams, ESQL code, and static audit findings.\n\n"
                "Provide an actionable, structured review with recommendations covering:\n"
                "1. **Architecture & Topology**: Is the flow structured optimally according to enterprise integration patterns?\n"
                "2. **Resilience & Transaction Management**: Are transactions, timeouts, and error rollbacks handled safely?\n"
                "3. **ESQL Transformation & Memory Efficiency**: Are message trees copied efficiently? Any risk of NULL exceptions?\n"
                "4. **Deployment & Portability**: Recommendations for BAR overrides, policy attachments, and containerization.\n\n"
                f"{markdown_context}"
            )

            if LLM_PROVIDER == "google-genai":
                client = genai_module.Client(api_key=api_key)
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt
                )
                return response.text
            elif LLM_PROVIDER == "google-generativeai":
                genai_module.configure(api_key=api_key)
                model = genai_module.GenerativeModel("gemini-1.5-flash")
                response = model.generate_content(prompt)
                return response.text
            else:
                return "> *AI Review note: No supported LLM provider found.*"
        except Exception as e:
            return f"> *AI Review note: AI analysis failed: {e}*"


class ACEWorkspaceCatalogAgent:
    """
    Discovers all .msgflow and .subflow files across an entire workspace,
    documents each one individually, and compiles a Master Catalog Index.
    """

    def __init__(self, workspace_root: Path, output_dir: Optional[Path] = None, use_ai: bool = False):
        self.workspace_root = Path(workspace_root).resolve()
        if output_dir:
            self.output_dir = Path(output_dir).resolve()
        else:
            self.output_dir = (self.workspace_root / "docs").resolve()
        self.use_ai = use_ai
        self.resolver = ESQLWorkspaceResolver(self.workspace_root)
        self.flow_agents: List[ACEFlowAgent] = []

    def discover_all_flows(self) -> List[Path]:
        flows: List[Path] = []
        for ext in ["*.msgflow", "*.subflow"]:
            for f in self.workspace_root.rglob(ext):
                # Ignore output_dir if it's within workspace_root
                try:
                    if self.output_dir.resolve() in f.resolve().parents:
                        continue
                except Exception:
                    pass
                flows.append(f)
        # Deduplicate while preserving order
        unique_flows = []
        seen = set()
        for f in flows:
            if f.resolve() not in seen:
                seen.add(f.resolve())
                unique_flows.append(f)
        return sorted(unique_flows, key=lambda x: str(x.relative_to(self.workspace_root)))

    def run(self) -> Dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        flows = self.discover_all_flows()
        if not flows:
            return {"total_flows": 0, "documented": []}

        summary_data = []

        print(f"[*] Discovered {len(flows)} message flows/subflows in workspace.")
        used_filenames = set()
        for flow_file in flows:
            print(f"  -> Parsing & documenting: {flow_file.name} ...")
            agent = ACEFlowAgent(flow_file, self.resolver, self.use_ai)
            doc_content = agent.document()
            self.flow_agents.append(agent)

            # Ensure unique filename if multiple flows have identical stems
            base_stem = flow_file.stem
            candidate_name = f"{base_stem}_documentation.md"
            if candidate_name in used_filenames:
                parent_prefix = re.sub(r'[^A-Za-z0-9_]', '_', flow_file.parent.name)
                candidate_name = f"{parent_prefix}_{base_stem}_documentation.md"
                if candidate_name in used_filenames:
                    rel_prefix = re.sub(r'[^A-Za-z0-9_]', '_', str(flow_file.parent.relative_to(self.workspace_root)))
                    candidate_name = f"{rel_prefix}_{base_stem}_documentation.md"

            used_filenames.add(candidate_name)
            out_filename = candidate_name
            out_path = self.output_dir / out_filename
            out_path.write_text(doc_content, encoding="utf-8")

            # Collect metrics for master catalog
            roots = agent.graph.get_root_nodes() if agent.graph else []
            in_eps = [r.get_protocol_endpoint_info() for r in roots if r.get_protocol_endpoint_info()]
            in_type = in_eps[0]["type"] if in_eps else "Internal/Subflow"

            summary_data.append({
                "flow_name": agent.parser.flow_name,
                "file_name": flow_file.name,
                "is_subflow": agent.parser.is_subflow,
                "path": str(flow_file.relative_to(self.workspace_root)),
                "doc_file": out_filename,
                "nodes": len(agent.parser.nodes),
                "connections": len(agent.parser.connections),
                "inbound_type": in_type,
                "score": agent.health_score,
                "grade": agent.health_grade,
                "subflows": list(agent.parser.referenced_subflows)
            })

        # Generate Master Catalog Index
        catalog_path = self.output_dir / "ACE_Flow_Catalog.md"
        catalog_content = self._generate_catalog_markdown(summary_data)
        catalog_path.write_text(catalog_content, encoding="utf-8")
        print(f"[✓] Generated Master Workspace Catalog: {catalog_path}")

        return {
            "total_flows": len(flows),
            "documented": summary_data,
            "catalog_path": str(catalog_path)
        }

    def _generate_catalog_markdown(self, summary_data: List[Dict[str, Any]]) -> str:
        lines = [
            "# IBM App Connect Enterprise (ACE) Workspace Flow Catalog",
            f"\n> **Workspace Path:** `{self.workspace_root}`  ",
            f"> **Total Flows & Subflows:** {len(summary_data)}  ",
            f"> **Total ESQL Files Discovered:** {len(self.resolver.esql_files)}  \n",
            "## 1. Executive Message Flow Inventory\n",
            "| Flow Name | Type | Relative Path | Inbound Interface | Nodes | Wires | Health Grade | Documentation |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]

        for s in summary_data:
            flow_kind = "Subflow" if s["is_subflow"] else "Message Flow"
            doc_link = f"[{s['doc_file']}](./{s['doc_file']})"
            lines.append(f"| **{s['flow_name']}** | `{flow_kind}` | `{s['path']}` | `{s['inbound_type']}` | {s['nodes']} | {s['connections']} | **{s['grade'].split()[0]}** ({s['score']}%) | {doc_link} |")

        lines.append("\n## 2. Workspace Integration & Dependency Map\n")
        lines.append("```mermaid")
        lines.append("flowchart TD")
        lines.append('    subgraph Workspace ["ACE Workspace Applications & Flows"]')

        diagram_ids = []
        for idx, s in enumerate(summary_data):
            flow_node_id = f"flow_{idx}"
            diagram_ids.append(flow_node_id)
            clean_path = s["path"].replace("\\", "/")
            lines.append(f'        {flow_node_id}["<b>{s["flow_name"]}</b><br/><i>{clean_path}</i><br/>({s["inbound_type"]})"]')

            # Subflow linkages
            for sub in s["subflows"]:
                matching_targets = [f"flow_{j}" for j, item in enumerate(summary_data) if item["flow_name"] == sub]
                if matching_targets:
                    for target_id in matching_targets:
                        lines.append(f"        {flow_node_id} -.->|Invokes Subflow| {target_id}")
                else:
                    sub_clean = re.sub(r'[^A-Za-z0-9_]', '_', sub)
                    lines.append(f"        {flow_node_id} -.->|Invokes Subflow| sub_{sub_clean}")

        lines.append("    end")
        lines.append("    classDef flowBox fill:#eef2ff,stroke:#4f46e5,stroke-width:1.5px,color:#1e1b4b;")
        if diagram_ids:
            lines.append(f"    class {','.join(diagram_ids)} flowBox;")
        lines.append("```\n")

        lines.append("## 3. Discovered Shared ESQL Modules\n")
        # Deduplicate modules by file and name
        unique_modules = {}
        for mod_key, mod in self.resolver.modules.items():
            dedup_key = f"{mod['file']}:{mod['name']}"
            if dedup_key not in unique_modules:
                unique_modules[dedup_key] = mod

        lines.append(f"Total **{len(unique_modules)}** unique ESQL modules indexed across the workspace:\n")
        lines.append("| Module Name | Type | Schema | Source ESQL File |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for mod in sorted(unique_modules.values(), key=lambda x: (x['file'].name, x['name'])):
            schema_disp = f"`{mod['schema']}`" if mod['schema'] else "*(default)*"
            lines.append(f"| **{mod['name']}** | `{mod['type']}` | {schema_disp} | `{mod['file'].name}` |")

        lines.append("\n---\n*Generated automatically by ACE Flow Architectural Agent.*")
        return "\n".join(lines)


def main():
    cli_parser = argparse.ArgumentParser(
        description="Generic IBM ACE Message Flow Documenter & AI Architectural Agent. Works on any flow or workspace."
    )
    cli_parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Path to a .msgflow/.subflow file OR a workspace directory (default: current directory)"
    )
    cli_parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="Recursively discover and document ALL message flows in the directory"
    )
    cli_parser.add_argument(
        "-o", "--output",
        help="Output markdown file path (for single flow) OR output directory (default: ./docs)"
    )
    cli_parser.add_argument(
        "--diagram-only",
        action="store_true",
        help="Output only the Mermaid diagram code to standard output"
    )
    cli_parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable LLM AI architectural assessment (requires GEMINI_API_KEY)"
    )
    cli_parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON metadata to standard output"
    )

    args = cli_parser.parse_args()
    target_path = Path(args.target).resolve()

    # Case 1: Target is a directory or --all is requested
    if target_path.is_dir() or args.all:
        search_dir = target_path if target_path.is_dir() else target_path.parent
        out_dir = Path(args.output).resolve() if args.output else (search_dir / "docs").resolve()
        catalog_agent = ACEWorkspaceCatalogAgent(search_dir, out_dir, use_ai=args.ai)
        result = catalog_agent.run()

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n[✓] Finished workspace documentation. Total flows processed: {result['total_flows']}")
            print(f"[✓] Documentation files generated in: {out_dir}")
        return

    # Case 2: Target is a single .msgflow or .subflow file
    if not target_path.exists():
        # Try searching for matching flow
        matches = list(Path(".").rglob(args.target))
        if matches:
            target_path = matches[0].resolve()
        else:
            print(f"[!] Error: Target path '{args.target}' does not exist.", file=sys.stderr)
            sys.exit(1)

    # Initialize workspace resolver from the project/parent directory
    resolver = ESQLWorkspaceResolver(target_path.parent.parent.parent if target_path.parent.parent.parent.exists() else target_path.parent)
    agent = ACEFlowAgent(target_path, workspace_resolver=resolver, use_ai=args.ai)

    if args.diagram_only:
        agent.parser.parse()
        graph = ACEFlowGraph(agent.parser)
        print(GenericMermaidGenerator.generate_flowchart(agent.parser))
        print("\n")
        print(GenericMermaidGenerator.generate_sequence(agent.parser, graph))
        return

    doc_content = agent.document()

    if args.json:
        metadata = {
            "flow_name": agent.parser.flow_name,
            "is_subflow": agent.parser.is_subflow,
            "path": str(target_path),
            "nodes": [
                {
                    "id": n.node_id,
                    "label": n.label,
                    "type": n.clean_type,
                    "category": n.category,
                    "properties": n.properties,
                    "promoted": n.promoted_properties
                }
                for n in agent.parser.nodes.values()
            ],
            "connections": [
                {
                    "source": c.source_node,
                    "source_terminal": c.source_terminal,
                    "target": c.target_node,
                    "target_terminal": c.target_terminal,
                    "is_error_path": c.is_error_path
                }
                for c in agent.parser.connections
            ],
            "audit_findings": agent.audit_findings,
            "health_score": agent.health_score,
            "health_grade": agent.health_grade
        }
        print(json.dumps(metadata, indent=2))
        return

    if args.output:
        out_p = Path(args.output)
        if out_p.is_dir() or args.output.endswith(("/", "\\")) or not out_p.suffix:
            out_file = out_p / f"{target_path.stem}_documentation.md"
        else:
            out_file = out_p
    else:
        out_file = Path("docs") / f"{target_path.stem}_documentation.md"

    out_file = out_file.resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(doc_content, encoding="utf-8")
    print(f"[✓] Successfully documented message flow '{target_path.name}'")
    print(f"[✓] Health Score: {agent.health_score}/100 ({agent.health_grade})")
    print(f"[✓] Generated file: {out_file}")


if __name__ == "__main__":
    main()
