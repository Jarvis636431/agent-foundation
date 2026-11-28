import { useCallback, useLayoutEffect } from "react";
import type { Schema } from "../transport/client";
import { isCodexRoute } from "../model-route";
import type { ThreadDraft } from "./draft";
import type { ModelControlValues } from "./model-controls";
import { initializeLegacyRunChoices } from "./legacy-run-choices";

type Configuration = {
  agent: string | undefined;
  inheritedModel: string | null | undefined;
};

/** Composer choices live in the shared document, never browser preferences. */
export function useComposerRunChoices(
  draft: ThreadDraft,
  agent: string | undefined,
  inheritedModel: string | null | undefined,
  refreshConfiguration?: () => Promise<Configuration | undefined>,
  legacyThreadId?: string,
  models?: readonly Pick<Schema<"ModelSummary">, "model_id" | "route">[],
) {
  const routeForModel = useCallback(
    (modelId?: string | null) =>
      models?.find((model) => model.model_id === (modelId ?? inheritedModel))
        ?.route,
    [models, inheritedModel],
  );
  const saved = draft.modelOptions;
  const savedKey = JSON.stringify(saved);
  useLayoutEffect(() => {
    if (legacyThreadId)
      initializeLegacyRunChoices(
        legacyThreadId,
        draft,
        agent,
        inheritedModel,
        routeForModel,
      );
  }, [
    draft,
    legacyThreadId,
    agent,
    inheritedModel,
    savedKey,
    draft.synchronized,
    routeForModel,
  ]);
  const mismatch = (configuration: Configuration) =>
    !!saved?.agent &&
    !!configuration.agent &&
    (saved.agent !== configuration.agent ||
      ((saved.model_id || configuration.inheritedModel !== undefined) &&
        (saved.model_id ?? configuration.inheritedModel) !==
          saved.effective_model));
  const mismatched = mismatch({ agent, inheritedModel });
  useLayoutEffect(() => {
    if (!mismatched) return;
    if (!refreshConfiguration) {
      draft.modelOptions = undefined;
      return;
    }
    // A draft update can arrive before the matching Thread detail refresh.
    // Verify against a fresh configuration before deleting shared choices.
    let cancelled = false;
    void refreshConfiguration().then((configuration) => {
      if (
        !cancelled &&
        configuration &&
        JSON.stringify(draft.modelOptions) === savedKey &&
        mismatch(configuration)
      )
        draft.modelOptions = undefined;
    });
    return () => {
      cancelled = true;
    };
  }, [
    draft,
    agent,
    inheritedModel,
    savedKey,
    mismatched,
    refreshConfiguration,
  ]);

  function controlsForModel(
    modelId: string | undefined,
    controls: ModelControlValues,
  ) {
    if (!isCodexRoute(routeForModel(modelId))) return controls;
    const { reasoning_mode: _mode, ...remaining } = controls;
    return remaining;
  }
  function choose(modelId: string | undefined, controls: ModelControlValues) {
    controls = controlsForModel(modelId, controls);
    draft.modelOptions =
      modelId || Object.values(controls).some((value) => value != null)
        ? {
            model_id: modelId,
            agent,
            effective_model: modelId ?? inheritedModel ?? undefined,
            ...controls,
          }
        : undefined;
  }
  return {
    routeForModel,
    effectiveControls: controlsForModel(draft.modelId, draft.controls),
    pending:
      !!saved?.agent &&
      (!agent ||
        (!saved.model_id && inheritedModel === undefined) ||
        (!!refreshConfiguration && mismatched)),
    onControlsChange: (controls: ModelControlValues) =>
      choose(draft.modelId, controls),
    onModelChange: (next: string | undefined) =>
      choose(
        next,
        (next ?? inheritedModel) === (draft.modelId ?? inheritedModel)
          ? draft.controls
          : {},
      ),
  };
}
