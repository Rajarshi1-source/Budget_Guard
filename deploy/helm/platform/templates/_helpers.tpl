{{- define "bg.image" -}}
{{ required "image.repository is required" .Values.image.repository }}@{{ required "image.digest is required" .Values.image.digest }}
{{- end -}}

{{- define "bg.podSecurity" -}}
securityContext: { runAsNonRoot: true, runAsUser: 10001, seccompProfile: { type: RuntimeDefault } }
{{- end -}}

{{- define "bg.containerSecurity" -}}
securityContext: { allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: [ALL] } }
{{- end -}}

{{/* A platform worker: same image, its own command, settings from the platform Secret. */}}
{{- define "bg.worker" -}}
apiVersion: apps/v1
kind: Deployment
metadata: { name: {{ .name }}, labels: { app: {{ .name }} } }
spec:
  replicas: {{ .replicas }}
  strategy: { type: RollingUpdate, rollingUpdate: { maxUnavailable: 1, maxSurge: 1 } }
  selector: { matchLabels: { app: {{ .name }} } }
  template:
    metadata: { labels: { app: {{ .name }}, budgetguard/role: platform } }
    spec:
      {{- include "bg.podSecurity" .root | nindent 6 }}
      containers:
        - name: {{ .name }}
          image: {{ include "bg.image" .root | quote }}
          args: {{ toJson .args }}
          env: [{ name: BG_PUBLIC_URL, value: {{ .root.Values.publicUrl | quote }} }]
          envFrom: [{ secretRef: { name: {{ .root.Values.secrets.platform }} } }]
          {{- with .ports }}
          ports: {{ toJson . }}
          {{- end }}
          resources: { requests: { cpu: 50m, memory: 128Mi }, limits: { memory: 384Mi } }
          {{- include "bg.containerSecurity" .root | nindent 10 }}
          volumeMounts: [{ name: tmp, mountPath: /tmp }]
      volumes: [{ name: tmp, emptyDir: {} }]
{{- end -}}
