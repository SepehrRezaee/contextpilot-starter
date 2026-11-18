# Live-demo helpers. Load once per PowerShell window (note the leading dot):
#     . .\presentation\demo.ps1
# Every helper sends ONE explicit request; nothing approves anything on its own.

$global:Api = 'http://127.0.0.1:8000'
$global:Backend = 'http://127.0.0.1:8765'
$global:Ctx = @{ tenant_id = 'northstar'; user_id = 'reviewer-01'; session_id = 'demo-a' }

function Invoke-Api([string]$Method, [string]$Url, $Body) {
    try {
        if ($null -ne $Body) {
            return Invoke-RestMethod $Url -Method $Method -ContentType 'application/json' -Body ($Body | ConvertTo-Json -Depth 8)
        }
        return Invoke-RestMethod $Url -Method $Method
    } catch {
        $status = $_.Exception.Response.StatusCode.value__
        Write-Host "HTTP $status  $($_.ErrorDetails.Message)" -ForegroundColor Red
    }
}

function Use-Session([string]$Id) { $global:Ctx.session_id = $Id; "session_id = $Id" }
function Use-Tenant([string]$Tenant) { $global:Ctx.tenant_id = $Tenant; "tenant_id = $Tenant" }
function Use-User([string]$User) { $global:Ctx.user_id = $User; "user_id = $User" }

# Ask the assistant; prints the important fields and returns the full response.
function Ask([string]$Message) {
    $body = $global:Ctx.Clone(); $body.message = $Message
    $r = Invoke-Api Post "$global:Api/assistant/respond" $body
    if ($null -eq $r) { return }
    Write-Host "intent: $($r.intent)   citations: $($r.citations -join ', ')   trace: $($r.trace_id)" -ForegroundColor Cyan
    Write-Host $r.answer
    if ($r.evidence) {
        $e = $r.evidence
        Write-Host "evidence: as_of=$($e.as_of) elapsed=$($e.elapsed_days) window=$($e.window_days) assessment=$($e.assessment) missing=$($e.missing -join ',')" -ForegroundColor DarkGray
    }
    if ($r.proposed_action) {
        $a = $r.proposed_action
        Write-Host "proposed_action: $($a.action_id)  status=$($a.status)  body_hash=$($a.body_hash)" -ForegroundColor Yellow
    }
    $global:Last = $r
    return $r
}

function Query-String { "tenant_id=$($global:Ctx.tenant_id)&user_id=$($global:Ctx.user_id)&session_id=$($global:Ctx.session_id)" }

# Inspect a stored draft (exact body, hash, status, next_step).
function Show-Action($Action) {
    $id = if ($Action -is [string]) { $Action } else { $Action.action_id }
    (Invoke-Api Get "$global:Api/assistant/actions/$id`?$(Query-String)").action | Format-List
}

# Separate, explicit approval of one action_id bound to its exact body hash.
function Approve($Action, [string]$Hash) {
    if (-not $Hash) { $Hash = $Action.body_hash }
    $body = $global:Ctx.Clone(); $body.body_hash = $Hash
    $r = Invoke-Api Post "$global:Api/assistant/actions/$($Action.action_id)/approve" $body
    if ($r) { $r.action | Select-Object status, attempts, idempotency_key, error_code, next_step | Format-List }
}

function Reconcile($Action) {
    $r = Invoke-Api Post "$global:Api/assistant/actions/$($Action.action_id)/reconcile" $global:Ctx.Clone()
    if ($r) { $r.action | Select-Object status, attempts, error_code, next_step | Format-List }
}

function Show-Memory {
    (Invoke-Api Get "$global:Api/assistant/users/$($global:Ctx.user_id)/memory?$(Query-String)").preferences | Format-Table key, value, source_session, updated_at
}

function Clear-Memory { Invoke-Api Post "$global:Api/assistant/users/$($global:Ctx.user_id)/memory/forget" $global:Ctx.Clone() }

# Ground truth straight from the fixture backend (not through the assistant).
function Show-Comments([string]$Ticket = 'NS-104') {
    $t = Invoke-Api Get "$global:Backend/v1/tenants/$($global:Ctx.tenant_id)/tickets/$Ticket"
    "ticket $Ticket version=$($t.version) comments=$(@($t.comments).Count)"
}

function Reset-Backend { Invoke-Api Post "$global:Backend/__admin/reset" @{} }

# Last trace line written by the API (default path var/traces.jsonl).
function Show-LastTrace([string]$Path = 'var/traces.jsonl') {
    $t = Get-Content $Path -Tail 1 | ConvertFrom-Json
    $t | Select-Object trace_id, tenant_id, user_id, session_id, intent, outcome, selected_document_ids, tools, action, error, duration_ms | Format-List
    $t.events | ForEach-Object { "{0,10} ms  {1}" -f $_.elapsed_ms, (($_ | ConvertTo-Json -Compress -Depth 5)) }
}

"ContextPilot demo helpers loaded. Context: $($global:Ctx.tenant_id)/$($global:Ctx.user_id)/$($global:Ctx.session_id)"
