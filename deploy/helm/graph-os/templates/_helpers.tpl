{{- define "graph-os.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "graph-os.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "graph-os.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "graph-os.labels" -}}
app.kubernetes.io/name: {{ include "graph-os.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end }}

{{- define "graph-os.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "graph-os.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "graph-os.image" -}}
{{- $image := . -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $image.repository $image.digest -}}
{{- else -}}
{{- printf "%s:%s" $image.repository (required "image.tag or image.digest is required" $image.tag) -}}
{{- end -}}
{{- end }}

{{/* The engine image: its own value, or the graph-os (unified) image. */}}
{{- define "graph-os.engineImage" -}}
{{- if .Values.engine.image.repository -}}
{{- include "graph-os.image" .Values.engine.image -}}
{{- else -}}
{{- include "graph-os.image" .Values.graphos.image -}}
{{- end -}}
{{- end }}

{{- define "graph-os.unified" -}}
{{- if eq .Values.topology "unified-in-process" }}true{{ end -}}
{{- end }}

{{- define "graph-os.sidecar" -}}
{{- if and (include "graph-os.unified" .) (eq .Values.engine.placement "sidecar") }}true{{ end -}}
{{- end }}

{{- define "graph-os.engineTcp" -}}
{{- if or (not (include "graph-os.unified" .)) .Values.engine.tcp.enabled }}true{{ end -}}
{{- end }}

{{/* Eunomia default per identity mode: none -> none, local/external -> embedded. */}}
{{- define "graph-os.eunomiaType" -}}
{{- if .Values.policy.eunomia.type -}}
{{- .Values.policy.eunomia.type -}}
{{- else if eq .Values.identity.mode "none" -}}
none
{{- else -}}
embedded
{{- end -}}
{{- end }}

{{/* Render-time guard rails. Each failure names the value to change. */}}
{{- define "graph-os.validate" -}}
{{- $ack := "I-UNDERSTAND-ANYONE-WHO-CAN-REACH-THIS-PORT-IS-ADMIN" -}}
{{- if and (eq .Values.identity.mode "none") (ne .Values.identity.noneExposeAck $ack) -}}
{{- fail (printf "identity.mode=none on Kubernetes needs identity.noneExposeAck=%s: a pod never binds loopback only, so anyone who reaches it is an administrator. Use local or external instead." $ack) -}}
{{- end -}}
{{- if and (include "graph-os.engineTcp" .) (not .Values.engine.tls.secretName) -}}
{{- fail "engine.tls.secretName is required: the engine refuses a non-loopback TCP listener without TLS (out-of-process-shared, or engine.tcp.enabled)" -}}
{{- end -}}
{{- if and (eq (include "graph-os.eunomiaType" .) "remote") (not .Values.policy.eunomia.remoteUrl) -}}
{{- fail "policy.eunomia.remoteUrl is required when the Eunomia type is remote" -}}
{{- end -}}
{{- if and (eq .Values.secrets.backend "vault") (not .Values.secrets.vaultUrl) -}}
{{- fail "secrets.vaultUrl is required when secrets.backend=vault (or set secrets.backend=engine)" -}}
{{- end -}}
{{- end }}

{{/* Engine argv shared by the sidecar and the shared StatefulSet. */}}
{{- define "graph-os.engineArgs" -}}
- epistemic-graph-server
- --persist-dir
- {{ if include "graph-os.unified" . }}/var/lib/graph-os/engine{{ else }}/var/lib/epistemic-graph/data{{ end }}
{{- if include "graph-os.unified" . }}
- --socket-path
- /run/epistemic-graph/epistemic-graph.sock
{{- end }}
{{- if include "graph-os.engineTcp" . }}
- --tcp-addr
- {{ printf "0.0.0.0:%v" .Values.engine.port }}
- --tcp-tls-cert
- /etc/graph-os/engine-tls/tls.crt
- --tcp-tls-key
- /etc/graph-os/engine-tls/tls.key
{{- end }}
{{- /* The engine refuses non-loopback auxiliary listeners; graph-os merges
       this exposition into its own /metrics in the unified topology. */}}
- --metrics-addr
- 127.0.0.1:9101
{{- with .Values.engine.extraArgs }}
{{ toYaml . }}
{{- end }}
{{- end }}
