# frozen_string_literal: true

require "json"
require "digest"
require "pathname"
require "set"
require "yaml"

ASSET_ROOT = Pathname.new(__dir__).realpath
REPOSITORY_ROOT = ASSET_ROOT.join("../..").cleanpath
MANIFEST_PATH = ASSET_ROOT.join("evals/manifest.yaml")
CANONICAL_HANDOFF = REPOSITORY_ROOT.join(
  ".github/schemas/cosmaneura-agent-handoff-v1.schema.json"
).cleanpath
CANONICAL_RESOLUTION = REPOSITORY_ROOT.join(
  ".github/schemas/cosmaneura-agent-resolution-v1.schema.json"
).cleanpath

EXPECTED_AGENTS = {
  "deploy-navigator-v1" => {
    "kind" => "deployment_assessment",
    "statuses" => %w[READY HOLD NEEDS_HUMAN INCOMPLETE]
  },
  "migration-sentinel-v1" => {
    "kind" => "migration_assessment",
    "statuses" => %w[READY HOLD NEEDS_HUMAN INCOMPLETE]
  },
  "release-medic-v1" => {
    "kind" => "release_assessment",
    "statuses" => %w[HEALTHY DEGRADED FAILED INCOMPLETE]
  },
  "security-vulnerability-sentinel-v1" => {
    "kind" => "security_assessment",
    "statuses" => %w[
      HOLD NEEDS_HUMAN PASS_WITH_FINDINGS NO_FINDINGS_OBSERVED INCOMPLETE
    ]
  }
}.freeze

REQUIRED_CASES = Set.new(%w[
  deploy-prompt-injection-and-ci-failure
  deploy-stale-sha
  deploy-missing-required-evidence
  migration-unsafe-contract-step
  migration-missing-rehearsal
  release-build-failure
  release-startup-failure
  release-missing-health-evidence
  security-cross-tenant-idor
  security-prompt-injection-and-ssrf
  security-missing-scanner-coverage
]).freeze

SHA = /\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z/
SHA256 = /\Asha256:[0-9a-f]{64}\z/
UNSUPPORTED_MODEL_SCHEMA_KEYWORDS = Set.new(%w[
  allOf anyOf oneOf if then else not dependentSchemas patternProperties
]).freeze
FOUNDRY_UNSUPPORTED_SCHEMA_CONSTRAINTS = Set.new(%w[
  format maxItems maxLength maximum minItems minLength minimum pattern uniqueItems
]).freeze
EXPECTED_LOCK_ROUTING = {
  "deployment_assessment" => {
    "name" => "deploy-navigator-v1",
    "producer_name" => "deploy-navigator",
    "routed_version" => "4"
  },
  "migration_assessment" => {
    "name" => "migration-sentinel-v1",
    "producer_name" => "migration-sentinel",
    "routed_version" => "1"
  },
  "release_assessment" => {
    "name" => "release-medic-v1",
    "producer_name" => "release-medic",
    "routed_version" => "1"
  },
  "security_assessment" => {
    "name" => "security-vulnerability-sentinel-v1",
    "producer_name" => "security-vulnerability-sentinel",
    "routed_version" => "2"
  }
}.freeze

def record(errors, condition, message)
  errors << message unless condition
end

def load_json(path, errors)
  JSON.parse(path.read, allow_nan: false)
rescue Errno::ENOENT, JSON::ParserError => error
  errors << "#{path.relative_path_from(REPOSITORY_ROOT)}: #{error.message}"
  nil
end

def load_yaml(path, errors)
  YAML.safe_load(
    path.read,
    permitted_classes: [],
    permitted_symbols: [],
    aliases: false
  )
rescue Errno::ENOENT, Psych::Exception => error
  errors << "#{path.relative_path_from(REPOSITORY_ROOT)}: #{error.message}"
  nil
end

def resolve_path(base, relative, errors, label)
  unless relative.is_a?(String) && !relative.empty?
    errors << "#{label}: expected a relative path"
    return nil
  end

  candidate = base.join(relative).cleanpath
  errors << "#{label}: path escapes repository" unless candidate.to_s.start_with?(
    "#{REPOSITORY_ROOT}/"
  )
  errors << "#{label}: missing #{relative}" unless candidate.file?
  candidate
end

def resolve_fragment(document, fragment)
  return document if fragment.nil? || fragment.empty?
  raise "unsupported fragment #{fragment}" unless fragment.start_with?("/")

  fragment.delete_prefix("/").split("/").reduce(document) do |value, raw|
    key = raw.gsub("~1", "/").gsub("~0", "~")
    raise "missing fragment #{fragment}" unless value.is_a?(Hash) && value.key?(key)

    value.fetch(key)
  end
end

def inspect_model_schema(node, path, source_path, documents, errors)
  case node
  when Hash
    node.each_key do |key|
      if UNSUPPORTED_MODEL_SCHEMA_KEYWORDS.include?(key)
        errors << "#{source_path}: #{path} uses unsupported model keyword #{key}"
      end
    end

    if node["type"] == "object"
      properties = node["properties"]
      required = node["required"]
      record(errors, properties.is_a?(Hash), "#{source_path}: #{path} object needs properties")
      record(
        errors,
        node["additionalProperties"] == false,
        "#{source_path}: #{path} must set additionalProperties=false"
      )
      if properties.is_a?(Hash)
        record(errors, required.is_a?(Array), "#{source_path}: #{path} needs required")
        if required.is_a?(Array)
          missing = properties.keys.to_set - required.to_set
          extra = required.to_set - properties.keys.to_set
          errors << "#{source_path}: #{path} optional properties #{missing.to_a.sort.join(', ')}" unless missing.empty?
          errors << "#{source_path}: #{path} unknown required properties #{extra.to_a.sort.join(', ')}" unless extra.empty?
        end
      end
    end

    reference = node["$ref"]
    if reference
      file_part, fragment = reference.split("#", 2)
      target_path = file_part.empty? ? source_path : source_path.dirname.join(file_part).cleanpath
      record(
        errors,
        target_path.to_s.start_with?("#{ASSET_ROOT.join('contracts')}/"),
        "#{source_path}: model $ref must stay in contracts: #{reference}"
      )
      target = documents[target_path.to_s] ||= load_json(target_path, errors)
      begin
        resolve_fragment(target, fragment) if target
      rescue RuntimeError => error
        errors << "#{source_path}: invalid $ref #{reference}: #{error.message}"
      end
    end

    node.each do |key, value|
      next if key.start_with?("x-")
      inspect_model_schema(value, "#{path}/#{key}", source_path, documents, errors)
    end
  when Array
    node.each_with_index do |value, index|
      inspect_model_schema(value, "#{path}/#{index}", source_path, documents, errors)
    end
  end
end

def deep_copy(value)
  case value
  when Hash
    value.to_h { |key, item| [key, deep_copy(item)] }
  when Array
    value.map { |item| deep_copy(item) }
  else
    value
  end
end

def canonical_json(value)
  normalized = case value
               when Hash
                 value.keys.sort.to_h { |key| [key, JSON.parse(canonical_json(value.fetch(key)))] }
               when Array
                 value.map { |item| JSON.parse(canonical_json(item)) }
               else
                 value
               end
  JSON.generate(normalized)
end

def sha256_text(value)
  "sha256:#{Digest::SHA256.hexdigest(value.encode("UTF-8"))}"
end

def bundle_model_schema(schema_path, documents)
  contracts_root = ASSET_ROOT.join("contracts").realpath

  resolve = nil
  resolve = lambda do |value, source_path, stack|
    if value.is_a?(Array)
      next value.map { |item| resolve.call(item, source_path, stack) }
    end
    next value unless value.is_a?(Hash)

    if value.key?("$ref")
      raise "schema $ref must not have siblings" unless value.keys == ["$ref"]
      reference = value.fetch("$ref")
      raise "unsupported schema reference #{reference.inspect}" unless reference.is_a?(String)
      raise "remote schema reference is forbidden" if reference.start_with?("http://", "https://")

      file_part, fragment = reference.split("#", 2)
      target_path = if file_part.nil? || file_part.empty?
                      source_path
                    else
                      source_path.dirname.join(file_part).cleanpath
                    end
      target_path = target_path.realpath
      unless target_path.to_s.start_with?("#{contracts_root}/") || target_path == contracts_root
        raise "draft schema reference escapes contracts: #{reference}"
      end
      target_document = documents[target_path.to_s] || JSON.parse(target_path.read)
      key = [target_path.to_s, fragment.to_s]
      raise "recursive schema reference #{reference}" if stack.include?(key)

      target = resolve_fragment(target_document, fragment)
      next resolve.call(deep_copy(target), target_path, stack + [key])
    end

    value.each_with_object({}) do |(key, item), result|
      next if ["$schema", "$id", "$comment", "$defs"].include?(key)
      next if key.start_with?("x-")
      result[key] = resolve.call(item, source_path, stack)
    end
  end

  root_path = schema_path.realpath
  document = documents[root_path.to_s] || JSON.parse(root_path.read)
  resolve.call(deep_copy(document), root_path, [[root_path.to_s, ""]])
end

def project_schema_for_foundry(schema)
  project = nil
  project = lambda do |rule|
    rule.each_with_object({}) do |(key, value), result|
      next if FOUNDRY_UNSUPPORTED_SCHEMA_CONSTRAINTS.include?(key)
      if key == "const"
        raise "schema combines const and enum" if rule.key?("enum")
        result["enum"] = [deep_copy(value)]
      elsif key == "properties"
        raise "schema properties must be an object" unless value.is_a?(Hash)
        result[key] = value.to_h do |property_name, property_schema|
          raise "invalid property schema" unless property_name.is_a?(String) && property_schema.is_a?(Hash)
          [property_name, project.call(property_schema)]
        end
      elsif ["items", "additionalProperties"].include?(key) && value.is_a?(Hash)
        result[key] = project.call(value)
      elsif ["anyOf", "oneOf"].include?(key)
        raise "schema #{key} must contain schema objects" unless value.is_a?(Array) && value.all? { |item| item.is_a?(Hash) }
        result[key] = value.map { |item| project.call(item) }
      else
        result[key] = deep_copy(value)
      end
    end
  end
  project.call(schema)
end

def validate_deployment_lock(manifest, documents, errors)
  lock_path = ASSET_ROOT.join("deployment.lock.json")
  lock = documents[lock_path.to_s] || load_json(lock_path, errors)
  return unless lock.is_a?(Hash) && manifest.is_a?(Hash)

  record(errors, lock["schema_version"] == "cosmaneura.foundry-agent-deployment.v1", "deployment lock schema version mismatch")
  record(errors, lock.dig("project", "name") == "CosmasCode/proj-default", "deployment lock project mismatch")
  record(errors, lock.dig("project", "model_deployment") == "gpt-5.6-sol", "deployment lock model mismatch")
  record(errors, lock.dig("runtime", "authorization") == "Entra", "deployment lock runtime must use Entra")
  record(errors, lock.dig("runtime", "response_storage") == false, "deployment lock runtime must set store=false")
  record(errors, lock.dig("runtime", "tools") == [], "deployment lock runtime tools must be empty")
  record(errors, lock.dig("runtime", "memory") == false, "deployment lock runtime memory must be false")

  locked_agents = lock["agents"]
  record(errors, locked_agents.is_a?(Hash), "deployment lock agents must be an object")
  return unless locked_agents.is_a?(Hash)
  record(errors, locked_agents.keys.to_set == EXPECTED_LOCK_ROUTING.keys.to_set, "deployment lock must contain exactly four assessment kinds")

  manifest_by_id = Array(manifest["agents"]).to_h { |entry| [entry["id"], entry] }
  EXPECTED_LOCK_ROUTING.each do |kind, expected_route|
    locked = locked_agents[kind]
    unless locked.is_a?(Hash)
      errors << "deployment lock is missing #{kind}"
      next
    end
    expected_route.each do |field, value|
      record(errors, locked[field] == value, "deployment lock #{kind}.#{field} must equal #{value.inspect}")
    end

    manifest_entry = manifest_by_id[expected_route["name"]]
    unless manifest_entry
      errors << "eval manifest is missing #{expected_route['name']}"
      next
    end
    definition_path = MANIFEST_PATH.dirname.join(manifest_entry["definition"]).cleanpath
    definition = load_yaml(definition_path, errors)
    next unless definition.is_a?(Hash)

    record(errors, definition.dig("output", "kind") == kind, "#{kind}: source kind mismatch")
    record(errors, definition.dig("agent", "id") == locked["name"], "#{kind}: source/lock agent name mismatch")
    record(errors, definition.dig("agent", "producer_name") == locked["producer_name"], "#{kind}: source/lock producer mismatch")
    record(errors, definition.dig("agent", "version") == locked["policy_version"], "#{kind}: source/lock policy version mismatch")

    prompt_paths = Array(definition.dig("instructions", "ordered_files")).map do |relative|
      definition_path.dirname.join(relative).cleanpath
    end
    instructions = prompt_paths.map do |path|
      path.read.gsub("\r\n", "\n").strip
    end.join("\n\n") + "\n"
    instruction_hash = sha256_text(instructions)

    schema_path = definition_path.dirname.join(
      definition.dig("output", "model_draft_schema_path")
    ).cleanpath
    begin
      full_schema = bundle_model_schema(schema_path, documents)
      projected_schema = project_schema_for_foundry(full_schema)
      full_hash = sha256_text(canonical_json(full_schema))
      projected_hash = sha256_text(canonical_json(projected_schema))
      record(errors, locked["output_schema_sha256"] == full_hash, "#{kind}: full schema hash is stale; expected #{full_hash}")
      record(errors, locked["foundry_schema_sha256"] == projected_hash, "#{kind}: projected Foundry schema hash is stale; expected #{projected_hash}")
    rescue Errno::ENOENT, JSON::ParserError, RuntimeError => error
      errors << "#{kind}: cannot render model schemas: #{error.message}"
    end
    record(errors, locked["instructions_sha256"] == instruction_hash, "#{kind}: instruction hash is stale; expected #{instruction_hash}")

    case_ids = Array(manifest_entry["cases"]).map do |relative|
      case_path = MANIFEST_PATH.dirname.join(relative).cleanpath
      eval_case = documents[case_path.to_s] || load_json(case_path, errors)
      eval_case.is_a?(Hash) ? eval_case["id"] : nil
    end
    record(errors, locked["synthetic_cases"] == case_ids, "#{kind}: synthetic case list differs from eval manifest")
  end

  inactive = lock["inactive_unrouted_versions"]
  if inactive.is_a?(Hash)
    active_versions = EXPECTED_LOCK_ROUTING.values.to_h { |entry| [entry["name"], entry["routed_version"]] }
    inactive.each do |name, versions|
      record(errors, active_versions.key?(name), "inactive version list has unknown agent #{name}")
      record(errors, versions.is_a?(Array) && versions.all? { |version| version.match?(/\A[1-9][0-9]*\z/) }, "inactive versions for #{name} are invalid")
      record(errors, !Array(versions).include?(active_versions[name]), "active version for #{name} is marked inactive")
    end
  end
end

errors = []
documents = {}

json_paths = ASSET_ROOT.glob("**/*.json").sort
yaml_paths = ASSET_ROOT.glob("**/*.{yaml,yml}").sort
json_paths.each { |path| documents[path.to_s] = load_json(path, errors) }
yaml_paths.each { |path| load_yaml(path, errors) }

record(errors, CANONICAL_HANDOFF.file?, "missing canonical handoff schema")
record(errors, CANONICAL_RESOLUTION.file?, "missing canonical resolution schema")
load_json(CANONICAL_HANDOFF, errors) if CANONICAL_HANDOFF.file?
load_json(CANONICAL_RESOLUTION, errors) if CANONICAL_RESOLUTION.file?

contract_paths = ASSET_ROOT.glob("contracts/*-output.schema.json").sort
record(errors, contract_paths.length == 4, "expected four model output contracts")
strict_definition_path = ASSET_ROOT.join("contracts/model-draft-definitions.schema.json")
strict_paths = contract_paths + [strict_definition_path]
strict_paths.each do |path|
  schema = documents[path.to_s]
  inspect_model_schema(schema, "$", path, documents, errors) if schema
end

contract_paths.each do |path|
  schema = documents[path.to_s]
  next unless schema
  final_path = resolve_path(
    path.dirname,
    schema["x-final-handoff-schema"],
    errors,
    "#{path}: x-final-handoff-schema"
  )
  record(errors, final_path == CANONICAL_HANDOFF, "#{path}: wrong final handoff schema")
end

manifest = load_yaml(MANIFEST_PATH, errors)
if manifest.is_a?(Hash)
  record(errors, manifest.dig("data_policy", "synthetic_only") == true, "manifest must require synthetic data")
  record(errors, manifest.dig("data_policy", "phi_allowed") == false, "manifest must prohibit PHI")
  record(errors, manifest.dig("data_policy", "secrets_allowed") == false, "manifest must prohibit secrets")
  record(errors, manifest.dig("execution", "network") == "disabled", "eval network must be disabled")
  record(errors, manifest.dig("execution", "tools") == "disabled", "eval tools must be disabled")
  record(errors, manifest.dig("execution", "trusted_postprocessing") == "required", "trusted postprocessing must be required")

  entries = manifest["agents"]
  record(errors, entries.is_a?(Array), "manifest agents must be an array")
  seen_agents = Set.new
  seen_cases = Set.new

  Array(entries).each do |entry|
    agent_id = entry["id"]
    expected = EXPECTED_AGENTS[agent_id]
    record(errors, !expected.nil?, "manifest has unexpected agent #{agent_id.inspect}")
    record(errors, seen_agents.add?(agent_id), "manifest duplicates agent #{agent_id.inspect}")

    definition_path = resolve_path(MANIFEST_PATH.dirname, entry["definition"], errors, "#{agent_id}: definition")
    contract_path = resolve_path(MANIFEST_PATH.dirname, entry["output_schema"], errors, "#{agent_id}: output_schema")
    definition = definition_path && load_yaml(definition_path, errors)
    contract = contract_path && documents[contract_path.to_s]

    if expected && definition.is_a?(Hash)
      record(errors, definition.dig("agent", "id") == agent_id, "#{agent_id}: definition ID mismatch")
      record(errors, definition.dig("agent", "version") == "1.0.0", "#{agent_id}: unexpected version")
      record(errors, definition.dig("foundry", "project") == "CosmasCode/proj-default", "#{agent_id}: wrong Foundry project")
      record(errors, definition.dig("foundry", "model", "deployment_name") == "gpt-5.6-sol", "#{agent_id}: wrong model deployment")
      record(errors, definition.dig("foundry", "execution", "tools") == [], "#{agent_id}: tools must be empty")
      record(errors, definition.dig("foundry", "execution", "stateless") == true, "#{agent_id}: must be stateless")
      record(errors, definition.dig("foundry", "execution", "advisory_only") == true, "#{agent_id}: must be advisory-only")
      record(errors, definition.dig("output", "kind") == expected["kind"], "#{agent_id}: kind mismatch")
      record(errors, definition.dig("output", "allowed_decisions") == expected["statuses"], "#{agent_id}: decision set mismatch")
      record(errors, definition.dig("output", "schema_strict") == true, "#{agent_id}: strict schema must be enabled")
      record(errors, definition.dig("output", "trusted_postprocessor", "required") == true, "#{agent_id}: postprocessor required")
      required_null_removals = Set.new(%w[
        actions[*].evidence[*].line_start
        actions[*].evidence[*].line_end
        actions[*].evidence[*].digest
      ])
      required_null_removals << "payload.rollback_reference" if expected["kind"] == "deployment_assessment"
      required_null_removals << "payload.revision" if expected["kind"] == "release_assessment"
      required_null_removals << "actions[*].cwe" if expected["kind"] == "security_assessment"
      actual_null_removals = Array(
        definition.dig(
          "output", "trusted_postprocessor", "remove_null_fields_before_final_validation"
        )
      ).to_set
      record(
        errors,
        required_null_removals.subset?(actual_null_removals),
        "#{agent_id}: nullable draft fields are not normalized before final validation"
      )
      record(errors, Array(definition.dig("controls", "prohibited_actions")).any?, "#{agent_id}: prohibited actions missing")

      Array(definition.dig("instructions", "ordered_files")).each do |relative|
        resolve_path(definition_path.dirname, relative, errors, "#{agent_id}: prompt")
      end
      configured_contract = resolve_path(
        definition_path.dirname,
        definition.dig("output", "model_draft_schema_path"),
        errors,
        "#{agent_id}: model draft schema"
      )
      record(errors, configured_contract == contract_path, "#{agent_id}: manifest/definition contract mismatch")
      configured_final = resolve_path(
        definition_path.dirname,
        definition.dig("output", "final_handoff_schema_path"),
        errors,
        "#{agent_id}: final schema"
      )
      record(errors, configured_final == CANONICAL_HANDOFF, "#{agent_id}: wrong canonical handoff path")
      configured_resolution = resolve_path(
        definition_path.dirname,
        definition.dig("output", "worker_resolution_schema_path"),
        errors,
        "#{agent_id}: resolution schema"
      )
      record(errors, configured_resolution == CANONICAL_RESOLUTION, "#{agent_id}: wrong resolution path")
    end

    if expected && contract.is_a?(Hash)
      record(errors, contract.dig("properties", "kind", "const") == expected["kind"], "#{agent_id}: contract kind mismatch")
      record(errors, contract.dig("properties", "decision", "properties", "status", "enum") == expected["statuses"], "#{agent_id}: contract status mismatch")
    end

    Array(entry["cases"]).each do |relative|
      case_path = resolve_path(MANIFEST_PATH.dirname, relative, errors, "#{agent_id}: eval case")
      next unless case_path
      eval_case = documents[case_path.to_s] || load_json(case_path, errors)
      next unless eval_case.is_a?(Hash)
      case_id = eval_case["id"]
      record(errors, seen_cases.add?(case_id), "duplicate eval case #{case_id.inspect}")
      record(errors, eval_case["agent"] == agent_id, "#{case_id}: agent mismatch")
      record(errors, eval_case["synthetic_non_phi"] == true, "#{case_id}: must be synthetic non-PHI")
      record(errors, eval_case["safety_critical"] == true, "#{case_id}: must be safety-critical")
      subject = eval_case.dig("input", "subject") || {}
      record(errors, subject["base_sha"].to_s.match?(SHA), "#{case_id}: invalid base SHA")
      record(errors, subject["head_sha"].to_s.match?(SHA), "#{case_id}: invalid head SHA")
      record(errors, eval_case.dig("input", "input_digest").to_s.match?(SHA256), "#{case_id}: invalid input digest")
      assertions = eval_case.dig("expected", "assertions")
      record(errors, assertions.is_a?(Array) && !assertions.empty?, "#{case_id}: assertions missing")
      if case_id.to_s.include?("prompt-injection")
        blocks_echo = Array(assertions).any? { |item| item["operator"] == "absent" }
        record(errors, blocks_echo, "#{case_id}: missing injection non-echo assertion")
      end
      serialized = JSON.generate(eval_case)
      record(errors, !serialized.match?(/(?:sk_live_|ghp_|Bearer )[A-Za-z0-9_-]{8,}/), "#{case_id}: credential-like fixture content")
    end
  end

  record(errors, seen_agents == EXPECTED_AGENTS.keys.to_set, "manifest agent set is incomplete")
  record(errors, REQUIRED_CASES.subset?(seen_cases), "manifest is missing required safety cases")
end

validate_deployment_lock(manifest, documents, errors)

if errors.empty?
  puts "VALID: #{json_paths.length} JSON assets, #{yaml_paths.length} YAML assets, #{REQUIRED_CASES.length} safety cases"
  exit 0
end

warn "INVALID Foundry agent assets (#{errors.length} error#{errors.length == 1 ? '' : 's'}):"
errors.each { |error| warn "- #{error}" }
exit 1
