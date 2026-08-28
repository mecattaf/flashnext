{
  description = "flashnext — the vendor FP8 release at TP=2 on a dual Strix Halo pair";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    # Consumed as an INPUT only (no license upstream — nothing may be copied,
    # and its `tuning` module is forbidden: it would fight the fleet's
    # 128 GiB GTT ceiling with a silent duplicate kernel parameter).
    nix-strix-halo = {
      url = "github:hellas-ai/nix-strix-halo";
      flake = true;
    };
  };

  outputs = { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      formatter.${system} = pkgs.nixfmt-tree or pkgs.nixfmt-rfc-style;

      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [ python3 jq rsync shellcheck ];
      };

      checks.${system} = {
        repo-tests = pkgs.runCommand "flashnext-repo-tests"
          { src = ./.; nativeBuildInputs = [ pkgs.python3 pkgs.bash ]; } ''
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
