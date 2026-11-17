import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const previewContainer = document.createElement("div");
previewContainer.id = "preview-fullscreen-container";
document.body.appendChild(previewContainer);

vi.mock("../../../utils/overlayContainer", () => ({
  fullscreenContainer: () => previewContainer,
}));

vi.mock("../../../hooks/useAuthImageSrc", () => ({
  useAuthImageSrc: () => ({
    src: "data:image/png;base64,AAAA",
    loadState: "ready",
    setSrc: vi.fn(),
  }),
}));

import { ToolMediaStrip } from "./ToolMediaStrip";

describe("ToolMediaStrip image preview", () => {
  afterEach(() => {
    cleanup();
    previewContainer.innerHTML = "";
  });

  it("mounts the preview inside the fullscreen container", async () => {
    const view = render(
      <ToolMediaStrip
        images={[
          {
            url: "/api/agents/agent_1/media/preview?source=outbound/a.png",
            filename: "a.png",
            kind: "image",
          },
        ]}
        videos={[]}
      />,
    );

    const previewImage = view.container.querySelector(".ant-image-img");
    expect(previewImage).not.toBeNull();
    fireEvent.click(previewImage!);

    await waitFor(() => {
      expect(
        previewContainer.querySelector(".ant-image-preview-wrap"),
      ).not.toBeNull();
    });
  });
});
