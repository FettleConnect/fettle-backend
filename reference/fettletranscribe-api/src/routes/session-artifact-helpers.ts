import { type Response } from "express";

export function sendSessionDownload(
  response: Response,
  download: {
    fileName: string;
    mimeType: string;
    data: Buffer;
  },
  options: {
    disposition?: "attachment" | "inline";
    noStore?: boolean;
  } = {}
) {
  if (options.noStore) {
    response
      .setHeader(
        "Cache-Control",
        "no-store, no-cache, must-revalidate, proxy-revalidate"
      )
      .setHeader("Pragma", "no-cache")
      .setHeader("Expires", "0");
  }

  response
    .setHeader(
      "Content-Disposition",
      `${options.disposition ?? "attachment"}; filename="${download.fileName}"`
    )
    .type(download.mimeType)
    .send(download.data);
}
