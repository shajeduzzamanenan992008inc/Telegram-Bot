#!/usr/bin/env ruby
# Standard-library-only validation for repository configuration and active workflows.
require "yaml"
require "json"

errors = []
checked = 0

# Parse all real YAML files in .github, including inactive profile examples.
yaml_files = (Dir.glob(".github/**/*.{yml,yaml}") + Dir.glob(".github/**/*.yml.example")).uniq.sort
yaml_files.each do |path|
  begin
    YAML.parse_file(path)
    checked += 1
  rescue StandardError => e
    errors << "#{path}: YAML parse error: #{e.message}"
  end
end

# Validate the kit's project profile using only Ruby's standard library.
profile_path = ".github/project-profile.json"
begin
  profile = JSON.parse(File.read(profile_path, encoding: "UTF-8"))
  raise "top-level value must be an object" unless profile.is_a?(Hash)
  raise "schemaVersion must be 1" unless profile["schemaVersion"] == 1
  raise "project must be an object" unless profile["project"].is_a?(Hash)
  raise "ci must be an object" unless profile["ci"].is_a?(Hash)
  raise "ci.checks must be an array" unless profile["ci"]["checks"].is_a?(Array)
  checked += 1
rescue StandardError => e
  errors << "#{profile_path}: profile validation error: #{e.message}"
end

# Copilot frontmatter must exist and declare the metadata appropriate to its type.
frontmatter_files = (
  Dir.glob(".github/agents/*.agent.md") +
  Dir.glob(".github/skills/*/SKILL.md") +
  Dir.glob(".github/instructions/*.instructions.md")
).sort

frontmatter_files.each do |path|
  begin
    content = File.read(path, encoding: "UTF-8")
    match = content.match(/\A---\s*\n(.*?)\n---\s*\n/m)
    raise "missing YAML frontmatter" unless match
    metadata = YAML.safe_load(match[1], permitted_classes: [], permitted_symbols: [], aliases: false)
    raise "frontmatter must be a mapping" unless metadata.is_a?(Hash)

    if path.end_with?(".agent.md") || path.end_with?("/SKILL.md")
      raise "agent/skill frontmatter requires name" unless metadata["name"].is_a?(String) && !metadata["name"].strip.empty?
      raise "agent/skill frontmatter requires description" unless metadata["description"].is_a?(String) && !metadata["description"].strip.empty?
    elsif path.end_with?(".instructions.md")
      raise "instruction frontmatter requires applyTo" unless metadata["applyTo"].is_a?(String) && !metadata["applyTo"].strip.empty?
    end
    checked += 1
  rescue StandardError => e
    errors << "#{path}: frontmatter error: #{e.message}"
  end
end

# Apply stricter policy only to active workflows. Inactive profile/template workflows
# are intentionally retained for manual review and must be adapted before activation.
active_workflows = Dir.glob(".github/workflows/*.{yml,yaml}").sort
active_workflows.each do |path|
  content = File.read(path, encoding: "UTF-8")

  errors << "#{path}: mutable action ref @main/@master/@HEAD is forbidden" if
    content.match?(/^\s*uses:\s*[^\s#]+@(main|master|HEAD)(?:\s|$)/i)

  errors << "#{path}: permissions: write-all is forbidden" if
    content.match?(/^permissions:\s*write-all\s*$/i)

  errors << "#{path}: pull_request_target requires a separate security review and is disabled by kit policy" if
    content.match?(/^\s*pull_request_target\s*:/)

  errors << "#{path}: declare explicit top-level permissions" unless
    content.match?(/^permissions:\s*$/)

  content.scan(/^\s*uses:\s*([^\s#]+)/).flatten.each do |reference|
    next if reference.start_with?("./", "docker://")
    next unless reference.include?("@")
    ref = reference.split("@", 2).last
    unless ref.match?(/\A[0-9a-f]{40}\z/i)
      errors << "#{path}: action must use a verified full 40-character commit SHA: #{reference}"
    end
  end

  checked += 1
end

if errors.empty?
  puts "Universal GitHub configuration validation passed (#{checked} YAML/JSON/frontmatter/workflow items)."
  exit 0
end

warn "Universal GitHub configuration validation failed:"
errors.each { |error| warn " - #{error}" }
exit 1
