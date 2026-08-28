{
  description = "flashnext — the vendor FP8 release at TP=2 on a dual Strix Halo pair";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # The engine source of record. `flake = false` — it is a source tree, not
    # a flake. This is Mechanism A of specs/flashnext/evidence/nix-packaging-brief.md
    # §2: the fork replaces upstream's `vllm-src` everywhere in the chain,
    # including nix-strix-halo's own `vllmPairBenchEnv`.
    vllm-fork = {
      url = "github:mecattaf/vllm/flashnext";
      flake = false;
    };

    # Consumed as an INPUT only (no license upstream — nothing may be copied,
    # and its `tuning` module is forbidden: it would fight the fleet's
    # 128 GiB GTT ceiling with a silent duplicate kernel parameter).
    #
    # Deliberately NOT `inputs.nixpkgs.follows` — see the brief §4.2.
    # vllm-rocm is built by mutating *nixpkgs'* python313Packages.vllm and
    # depends on that package's `.override` signature, on a patch file named
    # 0006-drop-rocm-extra-reqs.patch, and on six `--replace-fail` string
    # literals. Upstream's lock has not moved since 2026-07-21; buildability
    # against a current nixpkgs is UNDETERMINED. Let it keep its own pin and
    # accept the closure duplication.
    nix-strix-halo = {
      url = "github:hellas-ai/nix-strix-halo";
      flake = true;
      inputs.vllm-src.follows = "vllm-fork";
    };
  };

  outputs =
    { self, nixpkgs, nix-strix-halo, ... }@inputs:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      lib = nixpkgs.lib;

      # The version the running server must announce. `vllmVersion` is a
      # string literal at nix-strix-halo flake.nix:369, decoupled from the
      # src tag, so `follows` alone would leave the server reporting 0.25.1.
      # Rule 3 of the brief: set it alongside `version`.
      flashnextVersion = "0.29.0.dev-flashnext";

      # patches/ is the PROVENANCE MIRROR of the fork branch's commits past
      # base (scripts/verify-fork.sh enforces the count). The branch itself is
      # the build input (Mechanism A points src at it), so the mirror must NOT
      # be fed into the derivation's `patches` — every hunk is already in src
      # and patchPhase would abort on the double-apply. The mirror is read
      # here only to surface its count in the build log; a substrate-only
      # fixup that must ride OUTSIDE the branch belongs in a separate
      # directory wired in explicitly, never in patches/.
      patchDir = ./patches;
      mirrorPatchNames = lib.sort (a: b: a < b) (
        lib.filter (n: lib.hasSuffix ".patch" n) (builtins.attrNames (builtins.readDir patchDir))
      );

      # nix-strix-halo's own package set for this target — their nixpkgs,
      # their overlay stack, their fixpoint. `legacyPackages.<packageSuffix>`
      # is the documented escape hatch (flake.nix:543-553); gfx1151 is both a
      # valid suffix (pkgs/therock/targets.nix:41-52) and the default target.
      strixPkgs = nix-strix-halo.legacyPackages.${system}.gfx1151;

      # Mechanism C of the brief §2. `mkVllmTherock` takes 16 booleans and
      # nothing else, and `lib` exports no vLLM overlay builder, so an
      # attribute override is the only parameterisation point that exists.
      #
      # Three non-negotiable rules, all observed below:
      #   1. patches   are INHERITED UNTOUCHED — the nixpkgs patches the build
      #                                needs survive because we do not assign
      #                                the attribute; the engineering commits
      #                                ride in src (the fork branch), never in
      #                                the patch list (see patchDir note).
      #   2. postPatch is APPENDED   — therock-vllm.nix:257 *assigns* it and
      #                                :288-291 is load-bearing.
      #   3. VLLM_VERSION_OVERRIDE is set alongside `version` —
      #                                therock-vllm.nix:296 is what the
      #                                running server reports.
      flashnextVllmOverlay = final: prev: {
        vllm-rocm = prev.vllm-rocm.overridePythonAttrs (old: {
          src = inputs.vllm-fork;
          version = flashnextVersion;

          postPatch = (old.postPatch or "") + ''
            echo "flashnext: engine src = ${inputs.vllm-fork} (${
              inputs.vllm-fork.rev or "no-rev"
            })"
            echo "flashnext: version override = ${flashnextVersion}"
            echo "flashnext: provenance mirror patches/ = ${
              toString (builtins.length mirrorPatchNames)
            } (informational; engineering rides in the fork branch, not the patch list)"
            ${lib.optionalString (mirrorPatchNames == [ ]) ''
              echo "flashnext: WARNING - patches/ carries no mirrored .patch files."
              echo "flashnext: WARNING - either the fork branch is stock (check the"
              echo "flashnext: WARNING - vllm-fork rev above against the base commit"
              echo "flashnext: WARNING - 8e4e036a) or the mirror discipline lapsed;"
              echo "flashnext: WARNING - scripts/verify-fork.sh is the gate."
            ''}
          '';

          env = (old.env or { }) // {
            VLLM_VERSION_OVERRIDE = flashnextVersion;
          };
        });
      };

      # The overridden package, resolved through the same fixpoint that
      # defines the `vllm-rocm` alias (overlays/pkgs.nix:332).
      vllmFork = (strixPkgs.extend flashnextVllmOverlay).vllm-rocm;

      # Kill-switch / bisect lane. Mechanism A still points src at the fork
      # here (that is what `follows` does), but none of our overlay patches
      # are applied. `nix build .#vllm-fork-unpatched` answers "does the fork
      # tree itself build under this substrate?" without paying for our
      # patch set — the first question to ask when a HIP recompile aborts.
      vllmForkUnpatched = strixPkgs.vllm-rocm;

      # ---- devShells.engine ---------------------------------------------
      # Brief hazard 1: every change to src or to the patch list invalidates
      # the derivation and pays the few-hundred-HIP-kernel recompile again.
      # Budget is 2-3 rebuilds a night. This shell is the sanctioned inner
      # loop: iterate .py edits in a writable overlay dir that shadows the
      # built store path, and only fold a settled edit back into a .patch.
      enginePython = vllmFork.pythonModule;
      engineDeps = vllmFork.propagatedBuildInputs or (vllmFork.dependencies or [ ]);
      # The BUILT store path's site-packages, spelled out rather than folded
      # into makePythonPath: `requiredPythonModules` prepends the bare
      # interpreter, which would sit ahead of the engine on the search path.
      engineSitePackages = "${vllmFork}/${enginePython.sitePackages}";
      engineDepsPath = enginePython.pkgs.makePythonPath engineDeps;
      # therock-vllm.nix assigns the ROCm runtime library path into `env`;
      # mkDerivation flattens `env` onto drvAttrs, so read it back from there
      # rather than re-deriving a path we are not licensed to transcribe.
      # `or ""` keeps a shape change upstream a loud warning, not an eval abort.
      engineEnv = vllmFork.drvAttrs or { };
    in
    {
      formatter.${system} = pkgs.nixfmt-tree or pkgs.nixfmt-rfc-style;

      overlays.vllm-fork = flashnextVllmOverlay;

      packages.${system} = {
        vllm-fork = vllmFork;
        vllm-fork-unpatched = vllmForkUnpatched;
      };

      devShells.${system} = {
        default = pkgs.mkShell {
          packages = with pkgs; [ python3 jq rsync shellcheck ];
        };

        # nix develop .#engine
        engine = strixPkgs.mkShell {
          packages = [
            enginePython
            strixPkgs.jq
            strixPkgs.git
          ];

          shellHook = ''
            fn_overlay="$PWD/.engine-overlay"
            fn_sp='${engineSitePackages}:${engineDepsPath}'
            fn_ld='${engineEnv.LD_LIBRARY_PATH or ""}'
            fn_hip='${engineEnv.HIP_PATH or ""}'

            echo "flashnext engine shell — built engine: ${vllmFork}"
            echo "flashnext engine shell — version: ${flashnextVersion}"

            if [ "''${FN_ENGINE_OVERLAY:-1}" = "0" ]; then
              echo "flashnext engine shell — KILL-SWITCH FN_ENGINE_OVERLAY=0:"
              echo "flashnext engine shell — overlay dir NOT on PYTHONPATH; you are"
              echo "flashnext engine shell — running the sealed store path only."
              export PYTHONPATH="$fn_sp''${PYTHONPATH:+:$PYTHONPATH}"
            else
              mkdir -p "$fn_overlay"
              echo "flashnext engine shell — writable overlay FIRST on PYTHONPATH:"
              echo "flashnext engine shell —   $fn_overlay"
              echo "flashnext engine shell — then the built store path's site-packages."
              echo "flashnext engine shell — Edits under $fn_overlay SHADOW the sealed"
              echo "flashnext engine shell — build. Nothing you change here is in any"
              echo "flashnext engine shell — receipt until it is a .patch under patches/."
              export PYTHONPATH="$fn_overlay:$fn_sp''${PYTHONPATH:+:$PYTHONPATH}"
            fi

            if [ -n "$fn_ld" ]; then
              export LD_LIBRARY_PATH="$fn_ld''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            else
              echo "flashnext engine shell — WARNING: could not read the engine's"
              echo "flashnext engine shell — LD_LIBRARY_PATH off its drvAttrs. torch will"
              echo "flashnext engine shell — likely fail to load the TheRock runtime."
            fi
            [ -n "$fn_hip" ] && export HIP_PATH="$fn_hip"

            echo "flashnext engine shell — DO NOT use the wrapped 'vllm' binary in this"
            echo "flashnext engine shell — shell: its makeWrapper --prefix puts the sealed"
            echo "flashnext engine shell — site-packages ahead of the overlay and your"
            echo "flashnext engine shell — edits would be silently ignored. Invoke the"
            echo "flashnext engine shell — interpreter directly, e.g."
            echo "flashnext engine shell —   python -m vllm.entrypoints.openai.api_server ..."
          '';
        };
      };

      checks.${system} = {
        repo-tests =
          pkgs.runCommand "flashnext-repo-tests"
            {
              src = ./.;
              nativeBuildInputs = [ pkgs.python3 pkgs.bash ];
            }
            ''
              cp -r $src source && chmod -R u+w source && cd source
              python3 -m unittest discover -s tests -v
              touch $out
            '';
        # At most one ttm.pages_limit token may ever reach a host closure this
        # flake composes into; the forbidden upstream tuning module would add
        # a second. Enforced mechanically the day this flake grows a module.
      };
    };
}
