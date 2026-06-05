import {
  GetObjectCommand,
  PutObjectCommand,
  S3Client
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

export interface StoredPdfAsset {
  objectKey: string;
  url: string;
}

export interface PdfAssetStorage {
  storePdf(input: {
    sessionId: string;
    fileName: string;
    mimeType: string;
    data: Buffer;
  }): Promise<StoredPdfAsset>;
}

export class S3PdfAssetStorage implements PdfAssetStorage {
  private readonly client: S3Client;

  constructor(
    private readonly options: {
      region: string;
      bucketName: string;
      signedUrlTtlSeconds?: number;
    }
  ) {
    this.client = new S3Client({
      region: options.region
    });
  }

  async storePdf(input: {
    sessionId: string;
    fileName: string;
    mimeType: string;
    data: Buffer;
  }): Promise<StoredPdfAsset> {
    const objectKey = buildObjectKey(input.sessionId, input.fileName);

    await this.client.send(
      new PutObjectCommand({
        Bucket: this.options.bucketName,
        Key: objectKey,
        Body: input.data,
        ContentType: input.mimeType
      })
    );

    return {
      objectKey,
      url: await getSignedUrl(
        this.client,
        new GetObjectCommand({
          Bucket: this.options.bucketName,
          Key: objectKey
        }),
        {
          expiresIn: this.options.signedUrlTtlSeconds ?? 900
        }
      )
    };
  }
}

function buildObjectKey(sessionId: string, fileName: string): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const sanitizedFileName = fileName.replace(/[^a-zA-Z0-9._-]/g, "-");

  return `consultation-pdfs/${sessionId}/${stamp}-${sanitizedFileName}`;
}
